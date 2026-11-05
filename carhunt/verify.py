"""Verify new standing-watch hits by eye — Claude Code's eye — before anyone is told.

Until 2026-10-27 every raw-sweep hit went straight to the operator's Telegram
with "verify by eye before anything else". Measured that day: 175 hit pairs
across 82 watches, ONE real lead. The rest were a pickup's plate under a
Sportage heading, a Morning's plate matching a chassis tail, a VIN prefix
shared by every car of that model, owners' own comments, copies of one list.
So the alert was almost always noise, and the real check was always done by
hand in a Claude Code session: read the post around the number, compare make
/ model / plate region / list heading with the watch, decide.

This module does that check unattended:

  1. `scripts/raw_sweep.py` QUEUES every new (watch, post) pair here instead of
     alerting (`data/verify_queue.json`).
  2. `run()` groups the queue by watch + the text around the matched number
     (41 copies of one list line are one question, not 41), builds an evidence
     pack per group — excerpts, list heading, how many copies, whether the FULL
     identifier appears anywhere in text or OCR — and asks a headless
     `claude -p` for a structured verdict. No tools, no contact details.
  3. `own post` / `different car` (confident) -> written to
     `data/watch_reviewed.json` like a hand verdict (reviewed_by "claude-auto"),
     so the dashboard and report stop calling it a match.
     `possible sighting` -> stays visible as MATCH, the operator gets the lead
     card with the WhatsApp DRAFT (nothing is sent to the registrant).
     `unclear` / low confidence -> stays visible, flagged "needs your eye".
  4. ONE Telegram message per run with every verdict. Nothing before that.

The headless call strips ANTHROPIC_API_KEY so it runs on the claude.ai login
(subscription), never on a billed key. `verify:` in config.yaml sets the model.

    python -m carhunt.verify                     # process the queue
    python -m carhunt.verify --dry-run           # ask, print, write nothing
    python -m carhunt.verify --replay IDENT POST_ID [--show-evidence]
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

from . import notify, watches as watchlib
from .config import DATA_DIR, PROJECT_ROOT, load_config

QUEUE = DATA_DIR / "verify_queue.json"
LOG = DATA_DIR / "logs" / "verify.log"
VERDICTS = DATA_DIR / "verify_verdicts.jsonl"
AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
VERDICTS_OK = ("possible sighting", "own post", "different car", "unclear")
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
MAX_ATTEMPTS = 3


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def _cfg() -> dict:
    v = load_config().get("verify") or {}
    return {"model": v.get("model") or "opus",
            "max_groups_per_run": int(v.get("max_groups_per_run") or 12),
            "timeout_s": int(v.get("timeout_s") or 300),
            "min_confidence": float(v.get("min_confidence") or 0.6)}


# ------------------------------------------------------------------ queue --
def load_queue() -> list[dict]:
    try:
        return json.loads(QUEUE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def save_queue(items: list[dict]) -> None:
    QUEUE.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def enqueue(pairs) -> int:
    """`pairs` = iterable of (identifier, hit dict). Returns how many were new."""
    items = load_queue()
    have = {(i["identifier"], int(i["post_id"])) for i in items}
    reviewed = watchlib.load_reviewed()
    added = 0
    for ident, h in pairs:
        key = (ident, int(h["post_id"]))
        if key in have or key in reviewed:
            continue
        items.append({"identifier": ident, "post_id": int(h["post_id"]),
                      "why": h.get("why") or "", "kind": h.get("kind") or "",
                      "desc": h.get("desc") or "",
                      "queued_at": datetime.datetime.now().isoformat(timespec="seconds"),
                      "attempts": 0})
        have.add(key)
        added += 1
    save_queue(items)
    return added


# --------------------------------------------------------------- evidence --
def _token_for(item: dict) -> str:
    """The number the rule matched on, read back from the `why` string."""
    why = item.get("why") or ""
    m = re.search(r"(?:suffix|fragment|plate)\s+(\S+)", why)
    if m:
        return m.group(1).strip("()")
    qn, qp = watchlib.readings(item["identifier"])
    return qn or (sorted(qp, key=len)[-1] if qp else "")


def _norm(text: str) -> str:
    return (text or "").translate(AR_DIGITS)


def _positions(blob: str, token: str) -> list[int]:
    if not token:
        return []
    b = _norm(blob).upper()
    out = []
    for t in {token.upper(), token.upper().lstrip("0")}:
        if len(t) >= 4:
            out += [m.start() for m in re.finditer(re.escape(t), b)]
    return sorted(set(out))


def _context_key(blob: str, token: str) -> str:
    pos = _positions(blob, token)
    if not pos:
        return ""
    b = _norm(blob)
    s = b[max(0, pos[0] - 30):pos[0] + len(token) + 15]
    return re.sub(r"\s+", " ", s).strip()


def _heading(blob: str, pos: int) -> str:
    """The last line before `pos` that is mostly words, not numbers: on a
    كشف list that is the block heading ('إسبورتاج لوحة', 'كورولا')."""
    for line in reversed(blob[:pos].split("\n")[:-1][-40:]):
        line = line.strip(" /-–•\t")
        if not line:
            continue
        letters = len(re.findall(r"[A-Za-z؀-ۿ]", line))
        digits = len(re.findall(r"\d", _norm(line)))
        if letters >= 3 and letters > digits:
            return line[:80]
    return ""


def _count_anywhere(conn, needle: str) -> dict:
    """How many posts / OCR'd photos carry `needle` verbatim (Latin digits,
    or the same digits written in Arabic numerals)."""
    if not needle or len(needle) < 5:
        return {}
    ar = needle.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
    posts = conn.execute("SELECT count(*) FROM posts WHERE text LIKE ? OR text LIKE ?",
                         (f"%{needle}%", f"%{ar}%")).fetchone()[0]
    ocr = conn.execute("SELECT count(*) FROM media WHERE ocr_text LIKE ? OR ocr_text LIKE ?",
                       (f"%{needle}%", f"%{ar}%")).fetchone()[0]
    return {"needle": needle, "posts_with_it": posts, "photos_with_it": ocr}


def _post(conn, post_id: int) -> dict:
    row = conn.execute("SELECT id, source, url, text, seen_at FROM posts WHERE id = ?",
                       (post_id,)).fetchone()
    if not row:
        return {}
    ocr = conn.execute("SELECT group_concat(ocr_text, '\n---\n') FROM media WHERE post_id = ?"
                       " AND ocr_text IS NOT NULL AND ocr_text <> ''", (post_id,)).fetchone()[0]
    return {"id": row[0], "source": row[1], "url": row[2], "text": row[3] or "",
            "seen_at": row[4], "ocr": ocr or ""}


def evidence(conn, group: list[dict], watch: dict) -> dict:
    """Everything a reader needs, nothing they must not see (no contact)."""
    rep = group[0]
    post = _post(conn, rep["post_id"])
    token = _token_for(rep)
    excerpts, where = [], "post text"
    for field in ("text", "ocr"):
        blob = post.get(field) or ""
        pos = _positions(blob, token)
        if pos:
            where = "post text" if field == "text" else "photo OCR"
            for p in pos[:3]:
                excerpts.append({"heading_above": _heading(blob, p),
                                 "excerpt": _norm(blob[max(0, p - 350):p + len(token) + 350])})
            break
    if not excerpts:
        where = "not found verbatim (matched after normalisation)"
        excerpts.append({"heading_above": "", "excerpt": _norm(post.get("text", "")[:900])})
    qn, qp = watchlib.readings(watch["identifier"])
    checks = []
    full = qn or (sorted(qp, key=len)[-1] if qp else "")
    if full:
        checks.append(_count_anywhere(conn, full))
        if qn and len(qn) >= 11:                       # a VIN: its serial tail
            checks.append(_count_anywhere(conn, qn[-6:]))
    note = watch.get("note") or ""
    for run_ in re.findall(r"\d{5,}", _norm(note)):
        if run_ not in full and full not in run_ and not re.fullmatch(r"(19|20)\d\d", run_):
            c = _count_anywhere(conn, run_)
            if c:
                c["from"] = "a number in the registrant's note"
                checks.append(c)
    return {
        "watch": {"identifier": watch["identifier"], "note": note,
                  "registered": (watch.get("created_at") or "")[:10],
                  "chassis_reading": qn, "plate_serials": sorted(qp)},
        "rule": {"why": rep.get("why"), "matched_token": token,
                 "digits_in_token": sum(ch.isdigit() for ch in token),
                 "keyword_kind": rep.get("kind"), "stored_vehicle_desc": rep.get("desc")},
        "post": {"id": post.get("id"), "source": post.get("source"), "url": post.get("url"),
                 "seen_at": (post.get("seen_at") or "")[:10], "text_length": len(post.get("text", "")),
                 "first_200_chars": _norm(post.get("text", "")[:200]), "token_found_in": where,
                 "excerpts": excerpts},
        "copies": {"posts_in_this_group": len(group),
                   "post_ids": [g["post_id"] for g in group][:50]},
        "registry_wide_checks": [c for c in checks if c],
    }


PROMPT = """You are checking ONE automated hit for a missing-car watch in Sudan. A visitor registered an identifier (chassis number or plate) on a public lost-car site; a digit-matching rule found that number inside a scraped Facebook/Telegram post. Your job: read the evidence like a careful human and say whether this post could be a SIGHTING of the watched car, or is noise.

Vocabulary (choose exactly one):
- "possible sighting": the full identifier (or the plate serial with the same or an unreadable region) appears in a found / impound / recovery list (حصر, كشف, موجودات, لقيناها, الحوش, ساحة), or a post describing a car someone HAS, and nothing in the post contradicts the watch (make, model, colour, year, plate region).
- "own post": the number appears in the OWNER'S side of the story — a lost report (مفقودة, سرقت, بلاغ), a comment where someone lists their own missing car, a reply handing the chassis to a broker who asked "send us your chassis", a list of بلاغات. Right number, not a sighting.
- "different car": a concrete contradiction. Typical: the token sits under a PLATE heading or beside a region marker (خ١ … خ٩, لوحة) while the watch is a chassis; the token is only the tail or prefix of a longer number belonging to another make; only a VIN make/model prefix matches and the serial differs (a sister car); the post's make/model/colour/year disagree with the watcher's note; the plate region digit differs; Facebook alt-text of a screenshot glued phone status-bar digits onto the first list entry.
- "unclear": you cannot tell from the excerpt (no model named, region unreadable, number only partially shown).

Rules of judgement:
1. A matched token with fewer digits than the identifier is weak unless the make/model also agrees.
2. Lists are copied across dozens of groups; copies are not independent evidence.
3. `registry_wide_checks` tells you whether the FULL identifier appears anywhere in the corpus. If it appears nowhere and only a fragment matched here, lean "different car" or "unclear", never "possible sighting".
4. Be conservative about "possible sighting": say it only when the evidence supports it; "unclear" is an acceptable answer and a human will look.
5. Never speculate about people. Do not mention or infer contact details.

Answer with JSON: {"verdict": one of the four strings, "confidence": 0.0-1.0, "reason": at most 300 characters, in English, quoting the decisive Arabic words or numbers from the excerpt}.

EVIDENCE (JSON):
"""

SCHEMA = json.dumps({
    "type": "object",
    "properties": {"verdict": {"type": "string", "enum": list(VERDICTS_OK)},
                   "confidence": {"type": "number"},
                   "reason": {"type": "string"}},
    "required": ["verdict", "confidence", "reason"]})


def ask_claude(ev: dict, cfg: dict) -> dict:
    """One headless `claude -p` call, prompt on stdin, structured JSON back.

    Prompt goes over STDIN: the launcher is claude.cmd, so an argv prompt would
    pass through cmd.exe's 8 KB limit. The API key is stripped from the
    environment so the call runs on the claude.ai login (subscription), and
    the nested-session guard variables are dropped so it also works when
    launched from inside an interactive Claude Code session.
    """
    exe = shutil.which("claude")
    if not exe:                                     # Task Scheduler PATH can be shorter
        cand = os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd")
        exe = cand if os.path.exists(cand) else None
    if not exe:
        raise RuntimeError("claude CLI not on PATH")
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                        "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    prompt = PROMPT + json.dumps(ev, ensure_ascii=False, indent=1)
    r = subprocess.run([exe, "-p", "--output-format", "json", "--model", cfg["model"],
                        "--tools", "", "--json-schema", SCHEMA, "--no-session-persistence"],
                       input=prompt, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, cwd=PROJECT_ROOT, timeout=cfg["timeout_s"],
                       creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f"claude rc={r.returncode}: {r.stderr.strip()[-300:]}")
    out = json.loads(r.stdout)
    so = out.get("structured_output")
    if not so:
        m = re.search(r"\{.*\}", out.get("result") or "", re.S)
        so = json.loads(m.group(0)) if m else None
    if not so or so.get("verdict") not in VERDICTS_OK:
        raise RuntimeError(f"no usable verdict in: {(out.get('result') or '')[:200]}")
    so["confidence"] = float(so.get("confidence") or 0)
    so["reason"] = (so.get("reason") or "")[:400]
    so["model"] = cfg["model"]
    so["cost_usd_reported"] = out.get("total_cost_usd")
    return so


# ------------------------------------------------------------------ apply --
def _append_reviewed(entries: list[dict]) -> None:
    path = watchlib.REVIEWED_JSON
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        rows = []
    have = {(r.get("identifier"), r.get("post_id")) for r in rows}
    rows += [e for e in entries if (e["identifier"], e["post_id"]) not in have]
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _record(group: list[dict], verdict: dict, ev: dict, applied: str) -> None:
    VERDICTS.parent.mkdir(parents=True, exist_ok=True)
    first = ev["post"]["excerpts"][0]["excerpt"][:300] if ev["post"]["excerpts"] else ""
    with VERDICTS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": datetime.datetime.now().isoformat(timespec="seconds"),
                             "identifier": group[0]["identifier"],
                             "post_ids": [g["post_id"] for g in group],
                             "why": group[0].get("why"), "verdict": verdict,
                             "applied": applied, "context": first},
                            ensure_ascii=False) + "\n")


MARK = {"possible sighting": "✅ POSSIBLE SIGHTING", "own post": "✗ own post",
        "different car": "✗ different car", "unclear": "❓ unclear — needs your eye"}


def run(conn, watches=None, dry_run: bool = False) -> dict:
    """Process the queue. Returns counts: groups, leads, reviewed, unclear, failed, skipped."""
    cfg = _cfg()
    items = load_queue()
    stats = {"groups": 0, "leads": 0, "reviewed": 0, "unclear": 0, "failed": 0, "skipped": 0}
    if not items:
        return stats
    watches = watches or watchlib.load_watches()
    by_ident: dict = {}
    for w in watches:
        by_ident.setdefault(w["identifier"], w)
    reviewed = watchlib.load_reviewed()
    items = [i for i in items if (i["identifier"], int(i["post_id"])) not in reviewed]

    # group: same watch, same words around the number -> one question
    groups: dict = {}
    for it in items:
        post = _post(conn, it["post_id"])
        ctx = _context_key(post.get("text", "") + " " + post.get("ocr", ""), _token_for(it))
        groups.setdefault((it["identifier"], ctx or f"post:{it['post_id']}"), []).append(it)
    order = sorted(groups.values(), key=lambda g: min(x["post_id"] for x in g))
    todo, later = order[:cfg["max_groups_per_run"]], order[cfg["max_groups_per_run"]:]
    stats["skipped"] = sum(len(g) for g in later)

    lines = []
    remaining = [x for g in later for x in g]
    today = datetime.date.today().isoformat()
    for group in todo:
        ident = group[0]["identifier"]
        watch = by_ident.get(ident) or {"identifier": ident, "note": "", "contact": ""}
        try:
            ev = evidence(conn, group, watch)
            verdict = ask_claude(ev, cfg)
        except Exception as e:                      # keep for next run, 3 strikes
            log(f"FAILED {ident} post {group[0]['post_id']}: {e}")
            for g in group:
                g["attempts"] = int(g.get("attempts") or 0) + 1
            if group[0]["attempts"] >= MAX_ATTEMPTS:
                lines.append(f"⚠ verification failed {MAX_ATTEMPTS}x · {ident} · post "
                             f"{group[0]['post_id']} ({len(group)} copies) · check by hand")
                stats["failed"] += 1
            else:
                remaining += group
            continue
        stats["groups"] += 1
        v, conf = verdict["verdict"], verdict["confidence"]
        copies = f" (+{len(group) - 1} copies)" if len(group) > 1 else ""
        url = ev["post"].get("url") or "no saved link"
        if v in ("own post", "different car") and conf >= cfg["min_confidence"]:
            applied, shown = "reviewed", v
            if not dry_run:
                _append_reviewed([{"identifier": ident, "post_id": g["post_id"], "verdict": v,
                                   "why_it_hit": g.get("why"),
                                   "note": f"auto ({verdict['model']}, conf {conf:.2f}): {verdict['reason']}",
                                   "reviewed": today, "reviewed_by": "claude-auto"} for g in group])
            stats["reviewed"] += len(group)
        elif v == "possible sighting":
            applied, shown = "lead", v
            stats["leads"] += 1
            if not dry_run:
                hit = {"post_id": group[0]["post_id"], "why": group[0].get("why"),
                       "kind": group[0].get("kind")}
                try:
                    res = notify.notify_watch_hit(
                        watch, hit, conn, contact_registrant=False,
                        verdict_note=f"Claude ({verdict['model']}, conf {conf:.2f}): {verdict['reason']}")
                    log(f"lead card sent for {ident} post {hit['post_id']}: {res}")
                except Exception as e:
                    log(f"lead card failed: {e}")
        else:                                       # unclear, or not confident enough
            applied, shown = "unclear", "unclear"
            stats["unclear"] += 1
        lines.append(f"{MARK[shown]} · {ident} · post {group[0]['post_id']}{copies} · "
                     f"{v} conf {conf:.2f}\n   {verdict['reason']}\n   {url}")
        log(f"{applied}: {ident} post {group[0]['post_id']} x{len(group)} -> {v} ({conf:.2f}) "
            f"{verdict['reason'][:120]}")
        if not dry_run:
            _record(group, verdict, ev, applied)

    if not dry_run:
        save_queue(remaining)
    if lines:
        head = f"CarHunt verification · {stats['groups']} hit group(s) read"
        if dry_run:
            head += " · DRY RUN"
        if stats["skipped"]:
            head += f" · {stats['skipped']} more queued for the next run"
        text = head + "\n\n" + "\n\n".join(lines)
        if dry_run:
            print(text)
        else:
            ok = notify.send_telegram(text)
            log(f"summary telegram {'sent' if ok else 'NOT sent'}: {stats}")
    return stats


# ======================================================================
# LEADS ON THE FAMILY'S OWN CAR (added 2026-11-05) -- ADVICE ONLY
# ======================================================================
# Watch hits above are strangers' registrations, and a confident "different
# car" there is applied. A lead on the CAR THIS DEPLOYMENT SEARCHES FOR is
# different: wrongly dismissing a real sighting is the worst failure this
# system can have. So the same careful reading runs here -- this time it also
# LOOKS AT THE POST'S PHOTOS -- but its verdict is written onto the lead
# (`auto_eye_check` in the post's verdict) and sent to the operator, and
# nothing else changes: not the score, not the hit, not the lead's visibility.
# A human makes the call.
#
# Post 5169 is why: a "one digit off" lead flagged in August sat at score 75
# for ten weeks because nothing ever re-read it; the photo turned out to be a
# stock image and the number was typed in an unlabelled list.

TARGET_QUEUE = DATA_DIR / "verify_target_queue.json"
TARGET_VERDICTS = ("possible sighting", "own post", "different car", "unclear")
MAX_PHOTOS = 4
PHOTO_TYPES = {b"\xff\xd8\xff": ".jpg", b"\x89PNG": ".png", b"RIFF": ".webp", b"GIF8": ".gif"}


def _load(path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def enqueue_target(leads) -> int:
    """`leads` = iterable of dicts {post_id, why, source, token}. Returns how
    many were new. A post already read (by a human or by this check) is skipped."""
    items = _load(TARGET_QUEUE)
    have = {int(i["post_id"]) for i in items}
    added = 0
    for lead in leads:
        pid = int(lead["post_id"])
        if pid in have:
            continue
        items.append({"post_id": pid, "why": str(lead.get("why") or ""),
                      "source": str(lead.get("source") or ""), "token": str(lead.get("token") or ""),
                      "queued_at": datetime.datetime.now().isoformat(timespec="seconds"),
                      "attempts": 0})
        have.add(pid)
        added += 1
    TARGET_QUEUE.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    return added


def _target_token(item: dict) -> str:
    if item.get("token"):
        return item["token"]
    m = re.search(r"([0-9A-Za-z]{5,}) is one character from", item.get("why") or "")
    if m:
        return m.group(1)
    m = re.search(r"(?:chassis|plate)\s+([0-9A-Za-z]{4,})", item.get("why") or "")
    return m.group(1) if m else ""


def _photos(conn, post_id: int) -> list[str]:
    out = []
    for (path,) in conn.execute("SELECT path FROM media WHERE post_id = ? ORDER BY id", (post_id,)):
        try:
            head = open(path, "rb").read(4)
        except OSError:
            continue
        if any(head.startswith(sig) for sig in PHOTO_TYPES):
            out.append(path)
        if len(out) >= MAX_PHOTOS:
            break
    return out


def target_evidence(conn, item: dict, car: dict) -> dict:
    """What a careful reader needs about one lead on the family's car."""
    post = _post(conn, item["post_id"])
    token = _target_token(item)
    excerpts = []
    for field, where in (("text", "post text"), ("ocr", "photo OCR")):
        blob = post.get(field) or ""
        for p in _positions(blob, token)[:3]:
            excerpts.append({"in": where, "heading_above": _heading(blob, p),
                             "excerpt": _norm(blob[max(0, p - 350):p + len(token) + 350])})
    if not excerpts:
        excerpts.append({"in": "post text (number not found verbatim)", "heading_above": "",
                         "excerpt": _norm(post.get("text", "")[:900])})
    rows = conn.execute(
        "SELECT make, model, color, year, chassis, plate FROM vehicles WHERE post_id = ? LIMIT 400",
        (item["post_id"],)).fetchall()
    tok = token.upper()
    near_rows = [dict(zip(("make", "model", "color", "year", "chassis", "plate"), r)) for r in rows
                 if tok and any(tok in str(x or "").upper() for x in (r[4], r[5]))][:5]
    return {
        "target_car": {k: car.get(k) for k in ("make", "model", "year", "color", "vin", "plate",
                                                "registered_at")},
        "lead": {"why_flagged": item.get("why"), "how_found": item.get("source"),
                 "matched_number": token},
        "post": {"id": post.get("id"), "source": post.get("source"), "url": post.get("url"),
                 "seen_at": (post.get("seen_at") or "")[:10],
                 "first_300_chars": _norm(post.get("text", "")[:300]), "excerpts": excerpts,
                 "registry_rows_with_this_number": near_rows,
                 "vehicle_rows_in_post": len(rows)},
        "photos": [],     # filled with local file names by ask_claude_target
    }


TARGET_PROMPT = """You are helping a family whose car was taken in the Sudan war. An automated rule flagged ONE post as a possible lead on THEIR car (profile and identifiers in `target_car`). Read the evidence, LOOK AT EVERY PHOTO listed in `photos` (they are files in your working directory: open each with the Read tool), and give the family your honest reading.

Only an IDENTIFIER counts: the chassis/VIN or the plate. A white 2013-2016 Corolla looks like every other one, and swapping chassis plates is how stolen cars are laundered, so resemblance alone is never a sighting.

Choose exactly one:
- "possible sighting": the target's full chassis tail or VIN, or its plate (serial + the same or an unreadable region), is actually present in the post or a photo, in a found / impound / recovery / for-sale context, and nothing contradicts the target (make, model, colour, plate region).
- "own post": the number appears because the family themselves posted it (a lost report, a search comment).
- "different car": a concrete contradiction: the number differs from the target's at the full length it is written; it belongs to another make or model; it is a plate while the target number is a chassis (or the reverse); the photo shows a different vehicle or a document for another car.
- "unclear": the evidence cannot settle it (number only partly legible, photo unreadable, no context).

Rules: a number one character away from the target's is NOT the target unless a photo shows a misprint or misread of the target's own plate or stamp; typed text is not a misread. Describe what the photos show in one short clause each. Be conservative: "unclear" is acceptable. Never speculate about people and never mention contact details. Your answer is ADVICE: it never closes the lead; the family decides.

Answer with JSON: {"verdict": one of the four strings, "confidence": 0.0-1.0, "reason": at most 400 characters in English, quoting the decisive words or numbers, "photos_show": one short clause per photo}.

EVIDENCE (JSON):
"""

TARGET_SCHEMA = json.dumps({
    "type": "object",
    "properties": {"verdict": {"type": "string", "enum": list(TARGET_VERDICTS)},
                   "confidence": {"type": "number"},
                   "reason": {"type": "string"},
                   "photos_show": {"type": "array", "items": {"type": "string"}}},
    "required": ["verdict", "confidence", "reason"]})


def ask_claude_target(ev: dict, photo_paths: list[str], cfg: dict) -> dict:
    """Headless `claude -p` that may READ the post's photos and nothing else.

    The photos are copied into a fresh folder that becomes the working
    directory, and Read is the only tool: files outside the working directory
    need a permission nobody can grant in -p mode, so the reader cannot open
    config.yaml, the database or anything else. Subscription login, not an API key.
    """
    import tempfile
    exe = shutil.which("claude") or os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd")
    if not os.path.exists(exe):
        raise RuntimeError("claude CLI not on PATH")
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                        "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    work = tempfile.mkdtemp(prefix="carhunt_eye_")
    try:
        names = []
        for i, src in enumerate(photo_paths, 1):
            head = open(src, "rb").read(4)
            ext = next((e for sig, e in PHOTO_TYPES.items() if head.startswith(sig)), ".jpg")
            name = f"photo{i}{ext}"
            shutil.copyfile(src, os.path.join(work, name))
            names.append(name)
        ev = dict(ev, photos=names)
        prompt = TARGET_PROMPT + json.dumps(ev, ensure_ascii=False, indent=1)
        r = subprocess.run([exe, "-p", "--output-format", "json", "--model", cfg["model"],
                            "--tools", "Read" if names else "", "--json-schema", TARGET_SCHEMA,
                            "--no-session-persistence"],
                           input=prompt, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, cwd=work, timeout=cfg["timeout_s"],
                           creationflags=NO_WINDOW)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if r.returncode != 0:
        raise RuntimeError(f"claude rc={r.returncode}: {r.stderr.strip()[-300:]}")
    out = json.loads(r.stdout)
    so = out.get("structured_output")
    if not so:
        m = re.search(r"\{.*\}", out.get("result") or "", re.S)
        so = json.loads(m.group(0)) if m else None
    if not so or so.get("verdict") not in TARGET_VERDICTS:
        raise RuntimeError(f"no usable verdict in: {(out.get('result') or '')[:200]}")
    so["confidence"] = float(so.get("confidence") or 0)
    so["reason"] = (so.get("reason") or "")[:500]
    so["photos_show"] = [str(x)[:160] for x in (so.get("photos_show") or [])][:MAX_PHOTOS]
    so["photos_seen"] = len(names)
    so["model"] = cfg["model"]
    return so


def _write_eye_check(post_id: int, check: dict) -> None:
    """Record the advice on the post. Score, hit and visibility are untouched."""
    from . import db
    with db.write_tx() as w:
        row = w.execute("SELECT verdict FROM posts WHERE id = ?", (post_id,)).fetchone()
        v = json.loads(row[0]) if row and row[0] else {}
        v["auto_eye_check"] = check
        w.execute("UPDATE posts SET verdict = ? WHERE id = ?",
                  (json.dumps(v, ensure_ascii=False), post_id))


TARGET_MARK = {"possible sighting": "🚨 POSSIBLE SIGHTING — look now",
               "own post": "✗ your own post", "different car": "✗ looks like a different car",
               "unclear": "❓ unclear — needs your eye"}


def run_target(conn, car: dict | None = None, dry_run: bool = False, ask=None) -> dict:
    """Read every queued lead on the family's car. Returns counts."""
    cfg, ask = _cfg(), ask or ask_claude_target
    car = car or (load_config().get("car") or {})
    items = _load(TARGET_QUEUE)
    stats = {"read": 0, "failed": 0}
    if not items:
        return stats
    lines, remaining = [], []
    for item in items:
        try:
            photos = _photos(conn, item["post_id"])
            ev = target_evidence(conn, item, car)
            check = ask(ev, photos, cfg)
        except Exception as e:
            item["attempts"] = int(item.get("attempts") or 0) + 1
            log(f"TARGET check FAILED post {item['post_id']}: {e}")
            if item["attempts"] >= MAX_ATTEMPTS:
                lines.append(f"⚠ automatic check failed {MAX_ATTEMPTS}x · post {item['post_id']} "
                             f"· {item.get('why')} · please look by hand")
                stats["failed"] += 1
            else:
                remaining.append(item)
            continue
        check["at"] = datetime.datetime.now().isoformat(timespec="seconds")
        check["advice_only"] = True
        stats["read"] += 1
        url = ev["post"].get("url") or "no saved link"
        photos_line = ("\n   photos: " + " | ".join(check["photos_show"])) if check.get("photos_show") else ""
        lines.append(f"{TARGET_MARK[check['verdict']]} · post {item['post_id']} · conf "
                     f"{check['confidence']:.2f}\n   flagged: {item.get('why')}\n   "
                     f"{check['reason']}{photos_line}\n   {url}")
        log(f"TARGET advice post {item['post_id']}: {check['verdict']} ({check['confidence']:.2f})")
        if not dry_run:
            _write_eye_check(item["post_id"], check)
    if not dry_run:
        TARGET_QUEUE.write_text(json.dumps(remaining, ensure_ascii=False, indent=1), encoding="utf-8")
    if lines:
        text = ("CarHunt · YOUR CAR · automatic reading of " + str(len(lines)) + " lead(s)\n"
                "Advice only: every lead stays open until you decide.\n\n" + "\n\n".join(lines))
        if dry_run:
            print(text)
        else:
            ok = notify.send_telegram(text)
            log(f"target summary telegram {'sent' if ok else 'NOT sent'}: {stats}")
    return stats


# -------------------------------------------------------------------- CLI --
def _main(argv) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    from . import db
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="ask and print; write nothing, send nothing")
    ap.add_argument("--replay", nargs=2, metavar=("IDENT", "POST_ID"),
                    help="verify this one pair now (ignores the queue and the reviewed file)")
    ap.add_argument("--show-evidence", action="store_true")
    ap.add_argument("--target", action="store_true",
                    help="read the queued leads on the family's own car (advice only)")
    ap.add_argument("--queue-post", type=int, metavar="POST_ID",
                    help="queue one post as a lead on the family's car, then --target reads it")
    a = ap.parse_args(argv)
    conn = db.connect()
    if a.queue_post:
        row = conn.execute("SELECT json_extract(verdict, '$.identifier_hit'), "
                           "json_extract(verdict, '$.identifier_source') FROM posts WHERE id = ?",
                           (a.queue_post,)).fetchone()
        print("queued:", enqueue_target([{"post_id": a.queue_post, "why": (row or [None])[0] or "manual",
                                          "source": (row or [None, None])[1] or "manual"}]))
    if a.target:
        print(run_target(conn, dry_run=a.dry_run))
        return 0
    if a.queue_post:
        return 0
    if a.replay:
        ident, pid = a.replay[0], int(a.replay[1])
        all_w = watchlib.load_watches()
        watch = next((w for w in all_w if w["identifier"] == ident),
                     {"identifier": ident, "note": "", "contact": ""})
        hit = next((h for h in watchlib.load_raw_hits().get(ident, []) if int(h["post_id"]) == pid), None)
        if not hit:
            hits = watchlib.scan(conn, [watch])
            hit = next((h for h in hits.get(ident, []) if int(h["post_id"]) == pid),
                       {"post_id": pid, "why": "replay", "kind": ""})
        item = {"identifier": ident, "post_id": pid, "why": hit.get("why"),
                "kind": hit.get("kind"), "desc": hit.get("desc")}
        ev = evidence(conn, [item], watch)
        if a.show_evidence:
            print(json.dumps(ev, ensure_ascii=False, indent=1))
        print(json.dumps(ask_claude(ev, _cfg()), ensure_ascii=False, indent=1))
        return 0
    print(run(conn, dry_run=a.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
