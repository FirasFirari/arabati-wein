"""Clear the unscored backlog through OpenAI or Moonshot instead of Claude.

The normal path is Claude-first with OpenAI only as a failure fallback. This
runs the same two-tier logic deliberately on another provider, for when the
Claude budget is the thing that is blocked. Verdicts are saved in exactly the
shape the Claude path writes, so the registry, dashboard and alerts do not
care which provider answered — `provider` on each verdict records it, and
`tier` is prefixed with the provider name for the same reason.

`--provider moonshot` swaps in the Kimi client (carhunt/moonshot.py). Nothing
downstream of the call changes: same system prompt, same JSON contract, same
grounding gate, same deterministic identifier re-check, same meter.

What it is looking for: an IDENTIFIER — a chassis or plate number — either
written in the post text or legible in a photo. Not a resemblance. There is
no reference-photo comparison (matching.use_reference_photos is off): a white
2013-2016 Corolla looks like every other one, and chassis transplant is the
standard laundering method here.

So routing is by CONTENT, not by the screen's opinion:
  * text-only posts are screened on the cheap tier (luna), and escalate only
    if the screen flags them
  * any post WITH images goes straight to the deep tier (sol) at image detail
    "high" — a chassis stamp or a plate in a كشف screenshot is unreadable in
    the low-detail thumbnail the screen tier sees, so letting a cheap pass
    score such a post 0 would bury exactly what we are hunting for
  * whatever the model extracts is then re-checked DETERMINISTICALLY against
    the target's own identifiers, so a hit does not depend on the model also
    choosing to score the post highly
  * spend is metered from real usage after every call and the run STOPS at
    --budget, so a surprise cannot run away unattended
  * it is resumable: only unscored posts are picked up, and each verdict is
    saved as it arrives, so an interrupted run loses at most one call

  * every verdict from a TEXT-ONLY post goes through the grounding gate
    before it is written: any chassis/plate/contact/price whose digits are
    absent from that post's own text is nulled. Posts WITH photos skip the
    gate — a VIN read off a compliance plate is absent from the text by
    definition, and gating it there deletes the whole point of the pass

Usage:
    python scripts\\openai_backlog.py --limit 5 --budget 0.25   # rehearse
    python scripts\\openai_backlog.py                           # the backlog
    python scripts\\openai_backlog.py --provider moonshot --list-models
    python scripts\\openai_backlog.py --provider moonshot --limit 20 \\
        --workers 1 --budget 0.50                               # rehearse Kimi
"""
import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Every interesting thing this script prints — a post's reasons, a plate
# like '12345 خ1', a grounding-gate rejection — can contain Arabic, and
# the Windows console is cp1252. Without this, print() raises
# UnicodeEncodeError INSIDE a worker thread and kills it: a run died at
# 13 of 15 posts that way. The ALERT path prints `reasons`, so the
# untreated version could crash the pipeline exactly when it finds the
# car it is looking for.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from carhunt import db
from carhunt.analyzer import (MAX_POST_IMAGES, Analyzer, _image_block,
                              _parse_verdict)
from carhunt.config import load_config
from carhunt.gemini import GeminiClient
from carhunt.moonshot import MoonshotClient
from carhunt.vertex import VertexClient
from carhunt.normalize import norm_chassis
from carhunt.prefilter import identifier_hit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_local_verdicts import strip_ungrounded

# Which API is answering. Set once in main() before any worker starts; it
# labels every verdict's `provider` and `tier` so the registry records who
# said what — three providers have now written into this database and their
# error signatures are not the same.
PROVIDER = "openai"

# Sent with any post that has photos. The screen tier never sees this because
# it never sees a readable image.
IDENTIFIER_FOCUS = (
    "This post has photos. Read every number visible in them: chassis/VIN "
    "stamps, plate numbers, numbers chalked or written on the windscreen or "
    "bodywork, and every line of any screenshot of a كشف/حصر list. "
    "Transcribe digits EXACTLY as they appear, including Arabic-Indic "
    "numerals (٠١٢٣٤٥٦٧٨٩), and keep partial or blurred fragments as the "
    "fragment you actually see. Never complete, correct or guess a number, "
    "and never repeat an identifier from the target profile unless you can "
    "genuinely read it in this post — a wrong transcription costs more than "
    "a missing one. Put every number you read into \"vehicles\", one entry "
    "per distinct vehicle."
)

# USD per 1M tokens as (input, output, cached_input).
# OpenAI, from developers.openai.com/api/docs/pricing (2026-08-23): cached
# input bills at a tenth of input.
# Moonshot, from platform.kimi.ai/docs/pricing (2026-08-28): the cache
# discount is published per model and is NOT a flat tenth — k2.6 caches at
# $0.16 against $0.95, so assuming OpenAI's ratio would understate the bill.
PRICES = {
    "gpt-5.6-sol": (4.00, 20.00, 0.40),
    "gpt-5.6-luna": (0.20, 1.20, 0.02),
    "gpt-5.6-terra": (2.00, 12.00, 0.20),
    "gpt-5.4-mini": (0.75, 4.50, 0.075),
    "gpt-5-mini": (0.25, 2.00, 0.025),
    "kimi-k3": (3.00, 15.00, 0.30),
    "kimi-k2.6": (0.95, 4.00, 0.16),
    "kimi-k2.5": (0.60, 3.00, 0.10),
    "kimi-k2.7-code": (0.95, 4.00, 0.19),
    # Gemini, from ai.google.dev/pricing (2026-08-31). These are the PAID-tier
    # rates: on a free-tier AI Studio key the real spend is $0 and the meter
    # is a worst-case gauge — it exists so a key that turns out to belong to a
    # billed project cannot run away silently. Cached input bills at 1/4.
    # Flash-Lite is the only family with a usable FREE daily quota (500/day
    # against 20/day for full Flash), so it is the one that matters here.
    # An unlisted model falls back to the GPT-sol default of (4.00, 20.00) —
    # measured 2026-08-31, that overstated a Gemini run by roughly 10x and
    # made a free run look like it cost $0.66.
    # CORRECTED 2026-09-02 FROM A REAL INVOICE, after the guessed numbers
    # below cost real money. The meter reported ~$60 for work Google billed at
    # $388.37 (Vertex AI, Aug 25 - Sep 2) — roughly a 6x UNDERSTATEMENT — so
    # `--budget` did not stop the run before it burned the whole $300 trial
    # credit and pushed $224.10 onto the card. Billed SKU evidence:
    # Final invoiced rates, all three SKUs read off the September statement:
    #   Text Input   53,043,389 tokens = $79.56  -> $1.50/M
    #   Text OUTPUT  64,758,321 tokens = $485.69 -> $7.50/M   <-- the real cost
    #   Image Input  14,454,025 tokens = $21.68  -> $1.50/M
    # OUTPUT IS 5x INPUT AND DOMINATED THE BILL: $485.69 of a $646.12 gross.
    # The guess here was $3.00/M output, i.e. 2.5x low on the line item that
    # mattered most, which is why the meter read ~$60 for a $646 gross.
    # These are LIST rates. Do NOT bake in the ~91% Spending Based Discount
    # that Google applied afterwards - a discount is not a guarantee, and the
    # meter must estimate what could be owed, not what one invoice happened
    # to be reduced to.
    # NEVER put a guessed price in this table. An unlisted model falls back to
    # the GPT-sol default, which is safer than a low guess: it OVER-states.
    "gemini-3.5-flash-lite": (0.30, 2.50, 0.075),
    "gemini-3.1-flash-lite": (0.30, 2.50, 0.075),
    "gemini-3.5-flash": (1.50, 7.50, 0.375),
    "gemini-3.6-flash": (1.50, 7.50, 0.375),
    "gemini-3.7-flash": (1.50, 7.50, 0.375),
    "gemini-2.5-flash": (0.30, 2.50, 0.075),
    "gemini-2.5-flash-lite": (0.10, 0.40, 0.025),
}


def cost_of(usage: dict) -> float:
    inp, out, cache_price = PRICES.get(usage["model"], (4.00, 20.00, 0.40))
    cached = usage.get("cached_input", 0)
    fresh = max(usage["input"] - cached, 0)
    return (fresh * inp + cached * cache_price + usage["output"] * out) / 1_000_000


class Meter:
    """Running spend AND call count, safe to update from several threads.

    The call count is the trustworthy half. Spend is derived from PRICES, and
    a wrong price makes `exhausted` lie — which is exactly what happened on
    2026-09-02. A call ceiling needs no external fact to be correct.
    """

    def __init__(self, budget: float, max_calls: int = 0):
        self.budget = budget
        self.max_calls = max_calls
        self.spent = 0.0
        self.calls = 0
        self._lock = threading.Lock()

    def add(self, usage: dict | None) -> None:
        if not usage:
            return
        with self._lock:
            self.spent += cost_of(usage)
            self.calls += 1

    @property
    def exhausted(self) -> bool:
        if self.max_calls and self.calls >= self.max_calls:
            return True
        return self.spent >= self.budget


def previously_cleared(post_id: int) -> str | None:
    """Has a human already looked at this post's photos and ruled it out?"""
    row = db.connect().execute(
        "SELECT json_extract(verdict, '$.verified_by_eye') v "
        "FROM posts WHERE id = ?", (post_id,)).fetchone()
    return row["v"] if row else None


def _edit_distance_le1(a: str, b: str) -> bool:
    """Differ by at most one insert, delete, substitution — or a swap?

    Swapped neighbours count: transposition is how a human or a model
    misreads a long run of digits, and it is two substitutions, so plain
    edit distance would miss exactly the error we saw in practice.
    """
    if abs(len(a) - len(b)) > 1:
        return False
    if a == b:
        return True
    if len(a) == len(b):
        diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        if len(diff) == 2:
            i, j = diff
            if j == i + 1 and a[i] == b[j] and a[j] == b[i]:
                return True
    if len(a) > len(b):
        a, b = b, a
    i = j = 0
    edited = False
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        if edited:
            return False
        edited = True
        if len(a) == len(b):
            i += 1
        j += 1
    return True


def near_miss(value: str, car: dict) -> str | None:
    """One digit away from our chassis tail — i.e. what an OCR slip looks like.

    Measured: on a photographed VIN plate the two models read
    KNADM412AF5639477 and KNADM412AF6539471 — same plate, transposed digits.
    A single wrong character makes an exact match silently fail, so anything
    one edit away is worth a second, closer look rather than a shrug.
    """
    chassis = norm_chassis(car.get("vin") or "")
    if not chassis:
        return None
    tails = {chassis[-n:] for n in (6, 7, 8) if len(chassis) > n}
    token = norm_chassis(value)
    if len(token) < 6:
        return None
    for tail in tails:
        for window in {token, token[-len(tail):], token[:len(tail)]}:
            if len(window) >= 6 and window != tail and _edit_distance_le1(window, tail):
                return f"{token} is one character from target tail {tail}"
    return None


def _is_identifier(value: str) -> bool:
    """Is this a bare identifier, or the model talking?

    Models write PROSE into these fields — one returned
    "…fragments listed; no <full VIN> or <tail>", a sentence saying
    the VIN is ABSENT, and scanning it for the VIN naturally "matched". An
    identifier is a token or two, not a sentence.
    """
    v = (value or "").strip()
    if not v or len(v) > 30 or len(v.split()) > 2:
        return False
    return bool(re.search(r"[0-9٠-٩۰-۹]{5,}", v))


def extracted_identifiers(verdict: dict) -> list[str]:
    """Every identifier string the model claims to have read.

    `description` is mined too, not just the structured fields. Measured on
    Moonshot's first benchmark: post 22317 reads "دفار صغير 6605009" and the
    model returned chassis=null, plate=null, description="دفار صغير 6605009" —
    the number present, but nowhere the deterministic check could see it. A
    model that files the family's own chassis under prose instead of under
    `chassis` must not thereby escape the one check that is not an opinion, so
    digit runs are lifted out of the free text as candidates. This only ADDS
    candidates; nothing here can suppress a hit.
    """
    out = []
    ex = verdict.get("extracted") or {}
    if isinstance(ex, dict):
        out += [str(ex.get("vin") or ""), str(ex.get("plate") or "")]
    for v in verdict.get("vehicles") or []:
        if isinstance(v, dict):
            out += [str(v.get("chassis") or ""), str(v.get("plate") or "")]
            for run in re.findall(r"[0-9٠-٩۰-۹]{5,}",
                                  str(v.get("description") or "")):
                out.append(run)
    return [s for s in out if _is_identifier(s) and s.lower() != "none"]


def check_identifiers(verdict: dict, post: dict, car: dict) -> tuple | None:
    """Deterministic re-check of what the model read. Returns (hit, source).

    The model's score is an opinion; this is not. But it is only as good as
    the reading behind it, so the hit is graded by where it came from:
      "text"  — the identifier is really in the post text, so the free scan
                already knew and this is solid
      "image" — nothing in the text says it; the model read it off a photo.
                That is the case worth hunting for AND the case that can be
                invented, so it is flagged for a human to open the photo and
                never auto-confirmed.
    """
    values = extracted_identifiers(verdict)
    full_vin = norm_chassis(car.get("vin") or "")
    for value in values:
        hit = identifier_hit(value, car)
        if hit:
            in_text = identifier_hit(post.get("text"), car)
            if in_text:
                return hit, "text"
            # The FULL 17-character VIN "read" off a photo is almost always
            # the model reciting the target profile back: verified by eye on
            # post 9133, where the actual stampings were a truck frame rail
            # and a Mitsubishi floor pan. Posts here quote 6-7 digit tails
            # ("شاسي 022377"), never a whole VIN — so this shape is graded
            # down to a look-later, not a lead.
            if full_vin and norm_chassis(value) == full_vin:
                return hit, "prompt_echo"
            return hit, "image"
    for value in values:
        close = near_miss(value, car)
        if close:
            return close, "near_miss"
    return None


# A held-out set with known-good answers, for measuring a NEW provider before
# it is allowed near the registry. These posts were extracted by the local
# Sonnet/vision pipeline and passed its grounding gate, so their vehicle rows
# are the closest thing this project has to ground truth. Only posts that
# actually carry a chassis number are useful — a post with nothing to find
# cannot distinguish a good reader from a lazy one.
# Driven from `vehicles`, not from `posts`. There is no index on
# vehicles.post_id, so the obvious phrasing — EXISTS(SELECT 1 FROM vehicles
# WHERE post_id = p.id) — is a 310,000-row scan per post and does not finish:
# it ran for 15 minutes without reaching the first API call. This scans
# vehicles once via idx_vehicles_chassis and joins back by primary key.
# Whether the post has photos is decided in Python afterwards, which costs one
# cheap lookup per candidate instead of a second correlated scan.
BENCHMARK_SQL = """
SELECT DISTINCT v.post_id AS id
FROM vehicles v JOIN posts p ON p.id = v.post_id
WHERE v.chassis_norm IS NOT NULL
  AND json_extract(p.verdict, '$.tier') IN
      ('local_claude_sonnet', 'local_claude_vision', 'local_reextract')
ORDER BY v.post_id DESC
LIMIT 4000
"""

# Image posts worth re-reading: recovery sources only. The two big Telegram
# feeds are a general news channel and an auction board — 1,876 image posts
# between them and almost no recovery content, so they are left out.
RESCAN_SQL = """
SELECT p.id FROM posts p
WHERE EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id)
  AND (p.source LIKE 'facebook:%' OR p.source IN
       ('telegram:ftshly', 'telegram:losscarsudan', 'telegram:sudan_car',
        'telegram:alixxcar2020'))
  AND COALESCE(json_extract(p.verdict, '$.tier'), 'none') IN
      ('batch_screen', 'screen', 'prefiltered', 'none', 'duplicate')
ORDER BY p.id DESC
"""


def rescan_one(analyzer: Analyzer, post: dict, meter: Meter, model: str) -> dict:
    """Re-read one post's photos for identifiers, on the cheap tier at high detail."""
    fb = analyzer.fallback
    blocks: list[dict] = [{"type": "text", "text": "POST under evaluation:"}]
    if post["text"]:
        blocks.append({"type": "text", "text": f"Post text:\n{post['text'][:6000]}"})
    blocks.append({"type": "text", "text": IDENTIFIER_FOCUS})
    for path in (post["media"] or [])[:MAX_POST_IMAGES]:
        block = _image_block(path)
        if block:
            blocks.append(block)
    verdict = _parse_verdict(fb.complete(analyzer.system, blocks, deep=False,
                                         model=model, detail="high",
                                         effort="low"))
    meter.add(fb.last_usage)
    verdict["provider"] = PROVIDER
    verdict["tier"] = f"{PROVIDER}_image_rescan"
    return verdict


_OCR_CONN = None


def ocr_text_for(post_id: int) -> str:
    """OCR of this post's photos, as text. Empty string if there is none.

    Reuses ONE connection. This used to call db.connect() per post and never
    close it: fine at a 10k backlog, but at ~98k posts it stalled every run for
    10+ minutes before the first API call and piled up open connections.
    """
    global _OCR_CONN
    if _OCR_CONN is None:
        _OCR_CONN = db.connect()
    rows = _OCR_CONN.execute(
        "SELECT ocr_text FROM media WHERE post_id=? AND ocr_text IS NOT NULL "
        "AND ocr_text <> ''", (post_id,)).fetchall()
    return chr(10).join(r[0] for r in rows)


OCR_HEADER = (
    "TEXT READ FROM THIS POST'S PHOTOS BY OCR (Google Cloud Vision). This is a "
    "machine transcription of what is physically printed or stamped in the "
    "images - compliance plates, chassis stampings, numbers chalked on "
    "windscreens, screenshots of كشف lists. Treat it as part of the post. It "
    "is raw and may contain OCR errors, plate boilerplate and manufacturer "
    "text; copy identifiers from it EXACTLY as they appear and never repair "
    "them. If it contradicts the post text, report both rather than choosing."
)


def analyze_one(analyzer: Analyzer, post: dict, meter: Meter) -> dict:
    """Route by content: photos get the deep tier, text gets the cheap one."""
    fb = analyzer.fallback
    images = []
    for path in (post["media"] or [])[:MAX_POST_IMAGES]:
        block = _image_block(path)
        if block:
            images.append(block)

    blocks: list[dict] = [{"type": "text", "text": "POST under evaluation:"}]
    if post["text"]:
        blocks.append({"type": "text", "text": f"Post text:\n{post['text']}"})
    ocr = post.get("_ocr") or ""
    if ocr:
        # OCR REPLACES the images rather than joining them. Measured: this
        # model reads text far better than it reads photographs (8/17 exact
        # off images against 10/17 for a dedicated OCR engine), and a
        # text-only call costs ~$0.01 against ~$0.032 with photos attached.
        # The identifiers are the point, and they are now in the text.
        blocks.append({"type": "text",
                       "text": OCR_HEADER + chr(10) * 2 + ocr})
        images = []
    if images:
        blocks.append({"type": "text", "text": IDENTIFIER_FOCUS})
    blocks += images
    if len(blocks) == 1:
        return {"score": 0, "is_car_post": False, "tier": "empty",
                "reasons": "Empty post (no text or readable images)."}

    if images:
        # straight to the deep tier at high detail — this is the only pass
        # that can actually read a number off a photo
        verdict = _parse_verdict(fb.complete(analyzer.system, blocks, deep=True))
        meter.add(fb.last_usage)
        verdict["provider"] = PROVIDER
        verdict["tier"] = f"{PROVIDER}_image"
        return verdict

    verdict = _parse_verdict(fb.complete(analyzer.system, blocks, deep=False))
    meter.add(fb.last_usage)
    verdict["provider"] = PROVIDER

    if not analyzer._needs_deep_pass(verdict):
        verdict["tier"] = f"{PROVIDER}_screen"
        return verdict

    verdict = _parse_verdict(fb.complete(analyzer.system, blocks, deep=True))
    meter.add(fb.last_usage)
    verdict["provider"] = PROVIDER
    verdict["tier"] = f"{PROVIDER}_deep"
    return verdict


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = the whole backlog")
    ap.add_argument("--budget", type=float, default=8.0,
                    help="USD ceiling; the run stops when it is reached. This "
                         "is only as good as PRICES — see --max-calls.")
    ap.add_argument("--max-calls", type=int, default=2000,
                    help="HARD ceiling on API calls, independent of any price "
                         "table. Added 2026-09-02 after guessed prices made "
                         "--budget read ~6x low: the meter said $60 for work "
                         "billed at $388, so the budget never tripped, the "
                         "$300 trial credit was exhausted and $224.10 landed "
                         "on a card that then declined. A call count cannot be "
                         "wrong about itself.")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--newest-first", action="store_true",
                    help="score the most recent posts first (the scheduled "
                         "daily free lane uses this; intake outruns it)")
    ap.add_argument("--rescan-images", action="store_true",
                    help="re-read photos of ALREADY-scored recovery posts that "
                         "only ever got a cheap, low-detail look")
    ap.add_argument("--rescan-model", default=None,
                    help="model for the rescan (default: the screen tier)")
    ap.add_argument("--deep-model", default=None,
                    help="override the deep tier for this run. On a TEXT-only "
                         "queue the cheap tier is usually enough: measured, "
                         "luna and sol returned identical identifiers on 5 of "
                         "6 posts, and the one disagreement was reading a "
                         "photographed VIN plate — not text. ~15x cheaper.")
    ap.add_argument("--provider",
                    choices=("openai", "moonshot", "gemini", "vertex"),
                    default="openai",
                    help="which API answers. Everything downstream of the call "
                         "— grounding, identifier re-check, meter, DB writes — "
                         "is identical; only the client changes.")
    ap.add_argument("--screen-model", default=None,
                    help="override the cheap/screen tier for this run")
    ap.add_argument("--dry-run", type=Path, default=None, metavar="FILE",
                    help="write every verdict to FILE as JSONL and touch the "
                         "database NOT AT ALL. This is how a new provider gets "
                         "read and judged before it can write a single row "
                         "into the registry — an unreviewed provider has "
                         "fabricated identifiers on this project three times.")
    ap.add_argument("--benchmark", type=int, default=0, metavar="N",
                    help="score N posts that the local Sonnet/vision pipeline "
                         "ALREADY extracted chassis numbers from, instead of "
                         "the unscored queue. Gives every verdict a known-good "
                         "answer to be diffed against. Implies --dry-run "
                         "unless one is given.")
    ap.add_argument("--benchmark-images", action="store_true",
                    help="with --benchmark, draw the sample from posts WITH "
                         "photos (tests transcription off images) instead of "
                         "text-only posts")
    ap.add_argument("--skip-over-chars", type=int, default=0, metavar="N",
                    help="leave posts longer than N characters UNSCORED for "
                         "another provider. Measured on Moonshot 2026-08-28: "
                         "post 22474 (15,045 chars, 953 vehicles) ran 24 "
                         "minutes, hit the 64,000-token output ceiling and "
                         "returned nothing — reasoning tokens share that "
                         "budget with the answer. ~8000 is the safe line for "
                         "kimi-k2.6; the mega كشف lists belong on the local "
                         "subagent pipeline, which already handles them.")
    ap.add_argument("--use-ocr", action="store_true",
                    help="feed each post's stored Google Vision OCR text to "
                         "the analyzer INSTEAD of its photos, and extend the "
                         "grounding gate to cover it. This is the whole point "
                         "of the OCR sweep: an identifier read off a "
                         "compliance plate has always been ungroundable, "
                         "because it is absent from the post text by "
                         "definition, so the gate that caught 106 fabricated "
                         "identifiers had to be switched off for every post "
                         "with a photo. With OCR text stored beside the "
                         "image, that number now has something to be checked "
                         "against, and the gate covers the image half of the "
                         "corpus for the first time.")
    ap.add_argument("--verbose", action="store_true",
                    help="print every post as it starts and finishes. On a "
                         "long run this is the difference between diagnosing "
                         "a stall and guessing at one.")
    ap.add_argument("--list-models", action="store_true",
                    help="ask the provider what this key can call, then exit. "
                         "Worth doing first: model ids move, and a typo fails "
                         "once per post for the length of the backlog.")
    args = ap.parse_args()

    global PROVIDER
    PROVIDER = args.provider

    cfg = load_config()
    db.init()
    analyzer = Analyzer(cfg)
    if args.provider == "moonshot":
        # Same interface, different wire dialect — see carhunt/moonshot.py.
        analyzer.fallback = MoonshotClient(cfg)
        if not analyzer.fallback.available:
            sys.exit("Moonshot is not configured — set MOONSHOT_API_KEY or "
                     "moonshot.api_key in config.yaml.")
    elif args.provider == "vertex":
        # Gemini models billed as a Cloud service, which is the ONLY form the
        # free-trial credit pays for — AI Studio's Gemini API is excluded.
        # See carhunt/vertex.py.
        analyzer.fallback = VertexClient(cfg)
        if not analyzer.fallback.available:
            sys.exit(f"Vertex is not usable: {analyzer.fallback.why_unavailable()}")
    elif args.provider == "gemini":
        # Free-tier lane — see carhunt/gemini.py. UNBENCHMARKED until
        # --benchmark + audit_verdicts.py say otherwise; --dry-run only.
        analyzer.fallback = GeminiClient(cfg)
        if not analyzer.fallback.available:
            sys.exit("Gemini is not configured — set GEMINI_API_KEY or "
                     "gemini.api_key in config.yaml. Make a FREE key at "
                     "https://aistudio.google.com/apikey on a project "
                     "WITHOUT billing linked.")
    if not (analyzer.fallback and analyzer.fallback.available):
        sys.exit("OpenAI fallback is not configured (fallback.api_key).")
    if args.list_models:
        # Vertex's OpenAI-compatible shim has no /models endpoint, so its
        # client probes a candidate list instead of listing one.
        if args.provider == "vertex":
            print("\n".join(analyzer.fallback.list_models()))
            return
        client = analyzer.fallback.client
        print("\n".join(sorted(m.id for m in client.models.list().data)))
        return
    if args.deep_model:
        analyzer.fallback.model = args.deep_model
    if args.screen_model:
        analyzer.fallback.screen_model = args.screen_model

    # A benchmark that could write to the registry would defeat its own
    # purpose: the point is to read the provider's output BEFORE trusting it.
    if args.benchmark and not args.dry_run:
        args.dry_run = Path("data") / f"benchmark_{args.provider}.jsonl"
        print(f"--benchmark implies --dry-run; writing {args.dry_run}")
    if args.dry_run:
        args.dry_run.parent.mkdir(parents=True, exist_ok=True)
        args.dry_run.write_text("", encoding="utf-8")  # fresh file per run

    # An unpriced model means the meter is fiction. Refuse rather than run —
    # the previous behaviour (fall back to a default) is what let a 6x-wrong
    # price go unnoticed for a whole day.
    for label, mdl in (("deep", analyzer.fallback.model),
                       ("screen", analyzer.fallback.screen_model)):
        if mdl not in PRICES:
            sys.exit(f"REFUSING TO RUN: {label} model {mdl!r} has no entry in "
                     f"PRICES, so --budget cannot be enforced. Add the real "
                     f"billed rate (from an invoice, not a guess) first.")

    threshold = cfg["matching"]["alert_threshold"]
    car = cfg["car"]
    rescan_model = args.rescan_model or analyzer.fallback.screen_model

    if args.rescan_images:
        conn = db.connect()
        ids = [r["id"] for r in conn.execute(RESCAN_SQL).fetchall()]
        if args.limit:
            ids = ids[:args.limit]
        posts = [p for p in (db.post_with_media(i) for i in ids) if p]
        print(f"{len(posts)} already-scored recovery posts with photos to "
              f"re-read | model={rescan_model} at image detail HIGH "
              f"| budget ${args.budget:.2f}\n", flush=True)
    elif args.benchmark:
        conn = db.connect()
        print("selecting benchmark posts...", flush=True)
        ids = [r["id"] for r in conn.execute(BENCHMARK_SQL).fetchall()]
        posts = []
        for pid in ids:
            p = db.post_with_media(pid)
            if not p:
                continue
            if bool(p.get("media")) != bool(args.benchmark_images):
                continue
            posts.append(p)
            if len(posts) >= args.benchmark:
                break
        # Smallest first. A كشف list with 953 vehicles needs ~40,000 output
        # tokens and takes many minutes; with the big ones first, every worker
        # is busy on one and the run reports NOTHING for half an hour. Ordinary
        # posts finish in seconds and tell you whether the provider works at
        # all, which is the thing worth learning before paying for the rest.
        posts.sort(key=lambda p: len(p.get("text") or ""))
        kind = "with photos" if args.benchmark_images else "text-only"
        print(f"BENCHMARK: {len(posts)} {kind} posts the local pipeline already "
              f"read chassis numbers from | model="
              f"{analyzer.fallback.model if args.benchmark_images else analyzer.fallback.screen_model}"
              f" | budget ${args.budget:.2f}\n"
              f"Nothing is written to the database. Diff the output with:\n"
              f"  python scripts\\audit_verdicts.py {args.dry_run}\n", flush=True)
    else:
        posts = db.unanalyzed(limit=args.limit or 100000,
                              newest_first=args.newest_first)
        with_images = sum(1 for p in posts if p.get("media"))
        print(f"{len(posts)} unscored posts ({with_images} with images -> deep "
              f"tier) | screen={analyzer.fallback.screen_model} "
              f"deep={analyzer.fallback.model} | reference photos: "
              f"{'on' if analyzer.use_reference_photos else 'OFF'} "
              f"| budget ${args.budget:.2f}\n", flush=True)
    if args.skip_over_chars:
        too_big = [p for p in posts if len(p.get("text") or "") > args.skip_over_chars]
        posts = [p for p in posts if len(p.get("text") or "") <= args.skip_over_chars]
        if too_big:
            print(f"deferring {len(too_big)} post(s) over {args.skip_over_chars} "
                  f"chars, left unscored for another provider: "
                  f"{[p['id'] for p in too_big][:12]}", flush=True)

    if args.use_ocr:
        attached = 0
        for pst in posts:
            pst["_ocr"] = ocr_text_for(pst["id"])
            if pst["_ocr"]:
                attached += 1
        print(f"OCR text attached to {attached}/{len(posts)} posts; those go "
              f"to the TEXT tier with photos omitted, and their identifiers "
              f"are grounded against post text + OCR.", flush=True)

    meter = Meter(args.budget, args.max_calls)

    done = deep = errors = alerts = identifier_hits = ungrounded = 0
    lock = threading.Lock()

    def work(post: dict) -> None:
        nonlocal done, deep, errors, alerts, identifier_hits, ungrounded
        if meter.exhausted:
            return
        t0 = time.time()
        if args.verbose:
            print(f"  -> post {post['id']} ({len(post.get('text') or '')} chars, "
                  f"{len(post.get('media') or [])} images) started", flush=True)
        try:
            verdict = (rescan_one(analyzer, post, meter, rescan_model)
                       if args.rescan_images else analyze_one(analyzer, post, meter))
        except Exception as e:                      # keep the run alive
            with lock:
                errors += 1
            print(f"  post {post['id']} failed: {type(e).__name__} {str(e)[:120]}",
                  flush=True)
            return
        # A human already opened these photos and said no. Models re-read the
        # same image the same wrong way every time (post 2191's whitewashed
        # plate was claimed as the target serial on two separate runs), so an eye verdict
        # has to outrank them or the same false lead resurfaces forever.
        # GROUNDING GATE — text-only posts. An identifier the model returned
        # whose digits are nowhere in the post's own text was not read, it was
        # inferred: on the local pipeline this caught 106 fabricated
        # identifiers that ZERO agents flagged in their own self-reports.
        # It must NOT run on a post with photos — a VIN read off a
        # photographed compliance plate is absent from the text by definition,
        # and running the gate there nulled 251 real image-derived
        # identifiers on the first vision ingest. Runs BEFORE the identifier
        # re-check so an invented chassis cannot raise a false alert.
        # With OCR the gate applies to EVERY post, because a chassis
        # stamped on a plate is now present in text we hold. Without it, the
        # gate must still skip posts with photos or it deletes exactly what a
        # vision pass exists to find — 251 real identifiers were nulled that
        # way on the first vision ingest.
        grounding_source = post.get("text") or ""
        if args.use_ocr and post.get("_ocr"):
            grounding_source += chr(10) + post["_ocr"]
        gate_applies = bool(args.use_ocr and post.get("_ocr")) or             not (post.get("media") or [])
        if gate_applies:
            dropped = strip_ungrounded(verdict, grounding_source)
            if dropped:
                verdict["ungrounded_dropped"] = dropped[:20]
                with lock:
                    ungrounded += len(dropped)
                print(f"  post {post['id']}: nulled {len(dropped)} ungrounded "
                      f"field(s): {'; '.join(dropped[:3])}", flush=True)

        cleared = previously_cleared(post["id"])
        if cleared:
            verdict["verified_by_eye"] = cleared
            verdict["score"] = 0
            found = None
        else:
            found = check_identifiers(verdict, post, car)
        if found:
            hit, source = found
            verdict["identifier_hit"] = hit
            verdict["identifier_source"] = source
            if source == "text":
                verdict["score"] = max(verdict.get("score") or 0, 95)
            elif source == "near_miss":
                # one OCR slip away; worth human eyes, not an alert
                verdict["needs_photo_verification"] = True
                verdict["score"] = max(verdict.get("score") or 0, 75)
            elif source == "prompt_echo":
                verdict["needs_photo_verification"] = True
                verdict["likely_prompt_echo"] = True
                verdict["score"] = max(verdict.get("score") or 0, 60)
            else:
                # read off a photo and unconfirmed: high enough to surface,
                # explicitly NOT treated as a confirmed match until a human
                # opens the image (see lesson 1 — models invent identifiers)
                verdict["needs_photo_verification"] = True
                verdict["score"] = max(verdict.get("score") or 0, 85)
        if args.dry_run:
            # Not one row. Appended under the lock so an interrupted run keeps
            # everything it already paid for.
            line = json.dumps({"id": post["id"], "url": post.get("url"),
                               "verdict": verdict}, ensure_ascii=False)
            with lock:
                with open(args.dry_run, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        elif args.rescan_images:
            # This post already had a verdict. A re-read must only ever ADD:
            # keep the higher score, and keep the older vehicle rows if the
            # re-read found fewer, so a worse reading cannot quietly delete
            # registry entries or drop an existing candidate.
            conn2 = db.connect()
            prev = conn2.execute(
                "SELECT score FROM posts WHERE id = ?", (post["id"],)).fetchone()
            prev_score = (prev["score"] if prev else None) or 0
            prev_vehicles = conn2.execute(
                "SELECT COUNT(*) FROM vehicles WHERE post_id = ?",
                (post["id"],)).fetchone()[0]
            verdict["previous_score"] = prev_score
            verdict["score"] = max(verdict.get("score") or 0, prev_score)
            if len(verdict.get("vehicles") or []) >= prev_vehicles:
                db.save_analysis(post["id"], verdict)
            else:
                db.set_verdict(post["id"], verdict)
        else:
            db.save_analysis(post["id"], verdict)
        if args.verbose:
            print(f"  <- post {post['id']} done in {time.time() - t0:.0f}s: "
                  f"{len(verdict.get('vehicles') or [])} vehicles, "
                  f"score={verdict.get('score')}, ${meter.spent:.3f} so far",
                  flush=True)
        score = verdict.get("score") or 0
        with lock:
            done += 1
            if verdict.get("tier") in (f"{PROVIDER}_deep", f"{PROVIDER}_image"):
                deep += 1
            if found:
                identifier_hits += 1
                hit, source = found
                label = {"text": "IDENTIFIER IN TEXT",
                         "near_miss": "ONE DIGIT OFF - VERIFY BY EYE",
                         "prompt_echo": "full VIN echoed - probably not real",
                         "image": "READ FROM PHOTO - VERIFY BY EYE"}[source]
                print(f"\n  !! {label} post {post['id']}: {hit}\n"
                      f"    tier={verdict.get('tier')} "
                      f"images={len(post.get('media') or [])}\n"
                      f"    {post.get('url')}\n", flush=True)
            elif score >= threshold:
                alerts += 1
                print(f"\n  ALERT post {post['id']} score={score}\n"
                      f"    {str(verdict.get('reasons'))[:400]}\n"
                      f"    {post.get('url')}\n", flush=True)
            if done % 25 == 0:
                print(f"  {done}/{len(posts)} | {deep} deep | ${meter.spent:.3f} "
                      f"spent | {errors} errors", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, posts))

    print(f"\nscored {done} posts ({deep} on the deep tier), {errors} errors, "
          f"{identifier_hits} identifier hits, {alerts} over the alert threshold")
    print(f"grounding gate nulled {ungrounded} ungrounded field(s) on text-only posts")
    print(f"spend: ${meter.spent:.3f} across {meter.calls} calls"
          + ("  [BUDGET REACHED — rerun to continue]" if meter.exhausted else ""))


if __name__ == "__main__":
    main()
