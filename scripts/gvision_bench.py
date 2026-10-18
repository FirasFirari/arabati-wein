"""Measure Google Cloud Vision OCR against the local pipeline and Moonshot.

The question this answers is narrow and worth being precise about: **can a
dedicated OCR engine recover the chassis numbers that exist ONLY in a photo?**
Those are the ones that matter — 17 of them across 11 posts in the image
benchmark — because a number printed in the post text is already found for free
by the deterministic scan.

It is not a like-for-like contest. Cloud Vision transcribes; it has no idea
which number on a windscreen is a chassis and which is a phone number. So this
scores TRANSCRIPTION only: for each identifier the local vision pipeline
recorded off a photo, does Vision's raw OCR text contain it? That is exactly
the step Moonshot was measured to be bad at — every VIN it claimed off an image
alone came back mangled (`TGX02P860546153` for `JTGJX02P8G0504615`).

If Vision wins here the architecture changes for the better: OCR the image,
append the text to the post, and run the existing text analyzer over the
result. Image-derived identifiers then become TEXT-GROUNDED, which means the
grounding gate — currently skipped on any post with media, because a VIN read
off a plate is by definition absent from the post text — starts protecting the
image half of the corpus too. See the grounding-gate-vs-vision notes.

Credentials: an API key with the Cloud Vision API enabled, in the environment
as GOOGLE_VISION_API_KEY or as `google_vision.api_key` in config.yaml.

Usage:
    python scripts\\gvision_bench.py --limit 20
    python scripts\\gvision_bench.py --feature TEXT_DETECTION
    python scripts\\gvision_bench.py --dump data\\gvision_ocr.jsonl
"""
import argparse
import base64
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from carhunt import db
from carhunt.analyzer import sniff_media_type
from carhunt.config import load_config
from carhunt.normalize import norm_chassis
from openai_backlog import _edit_distance_le1

ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

# Vision's own limit is 20MB per image and 41MB per request; this project's
# media are Facebook/Telegram downloads well under that, but a corrupt or
# HTML-error-page "image" would be sent as garbage and billed, so every file
# is sniffed by magic bytes first — the same check the analyzer uses.
MAX_IMAGE_BYTES = 18 * 1024 * 1024


def api_key(cfg: dict) -> str | None:
    return (os.environ.get("GOOGLE_VISION_API_KEY")
            or (cfg.get("google_vision") or {}).get("api_key") or None)


def ocr_image(path: str, key: str, feature: str) -> tuple[str, str | None]:
    """Return (text, error). One image, one Vision call."""
    p = Path(path)
    if not p.is_file():
        return "", "missing file"
    raw = p.read_bytes()
    if sniff_media_type(raw[:16]) is None:
        return "", "not a real image"
    if len(raw) > MAX_IMAGE_BYTES:
        return "", f"too large ({len(raw)} bytes)"
    body = {"requests": [{
        "image": {"content": base64.standard_b64encode(raw).decode()},
        "features": [{"type": feature}],
        # Sudanese posts mix Arabic script, Arabic-Indic digits and Latin VINs.
        # Without hints Vision tends to force one script and drop the other.
        "imageContext": {"languageHints": ["ar", "en"]},
    }]}
    try:
        r = requests.post(f"{ENDPOINT}?key={key}", json=body, timeout=90)
    except Exception as e:
        return "", f"{type(e).__name__}: {e}"
    if r.status_code != 200:
        return "", f"HTTP {r.status_code}: {r.text[:200]}"
    resp = (r.json().get("responses") or [{}])[0]
    if "error" in resp:
        return "", f"api error: {resp['error'].get('message', '')[:200]}"
    ann = resp.get("fullTextAnnotation") or {}
    if ann.get("text"):
        return ann["text"], None
    texts = resp.get("textAnnotations") or []
    return (texts[0].get("description", "") if texts else ""), None


def digits_of(s: str) -> str:
    return re.sub(r"\D", "", (s or "").translate(ARABIC_DIGITS))


# Characters an OCR engine confuses on stamped metal. O/I/Q are the safe half:
# the VIN standard EXCLUDES them precisely because they misread as 0 and 1, so
# any O in a VIN-shaped string is already an error. B/S/Z/G are the risky half
# — they are legal VIN characters — so a match that needs them is reported as
# "near", never as exact. Without this fold, post 22330's Vision read
# "MMBMGZ SH5ADO17257" scored a MISS against "MM8MG25H5AD017287", which is the
# same VIN.
VIN_FOLD = str.maketrans("OQIBSZG", "0018526")


def vin_fold(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", s).upper().translate(VIN_FOLD)


def found_in(needle: str, haystack: str) -> str:
    """Is this identifier in the OCR text? Returns 'exact', 'near' or ''.

    Compared two ways because a VIN is alphanumeric and a chassis tail is not:
    the raw string (case-folded, separators stripped) catches
    'KMHBU51HP5U327594', and the digits-only projection catches '٠٤٣٨٠٦'
    written in Arabic-Indic numerals. 'near' means one character out — the
    same tolerance near_miss() uses, because that is what an OCR slip looks
    like and it is worth reporting separately rather than scoring as a miss.
    """
    n_raw = re.sub(r"[^A-Za-z0-9]", "", needle).upper()
    h_raw = re.sub(r"[^A-Za-z0-9]", "", haystack).upper()
    if n_raw and n_raw in h_raw:
        return "exact"
    n_dig, h_dig = digits_of(needle), digits_of(haystack)
    # The digits-only projection is for chassis TAILS ("٠٤٣٨٠٦"), which are
    # all digits anyway. It must NOT be applied to a full alphanumeric VIN:
    # KMHBU51HP5U327594 and KMHDB51HP5U327594 are different vehicles that
    # project to the same digit string, so allowing it here would have scored
    # the exact error Moonshot made as a perfect read.
    if not re.search(r"[A-Za-z]", needle) and len(n_dig) >= 5 and n_dig in h_dig:
        return "exact"
    # one-character-out scan over same-length windows
    n_fold, h_fold = vin_fold(needle), vin_fold(haystack)
    for hay, nee in ((h_raw, n_raw), (h_dig, n_dig), (h_fold, n_fold)):
        if len(nee) < 6 or len(hay) < len(nee):
            continue
        if nee in hay:
            return "near"          # matched only after folding confusions
        for i in range(len(hay) - len(nee) + 1):
            if _edit_distance_le1(hay[i:i + len(nee)], nee):
                return "near"
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--feature", default="DOCUMENT_TEXT_DETECTION",
                    choices=("DOCUMENT_TEXT_DETECTION", "TEXT_DETECTION"),
                    help="DOCUMENT_ is tuned for dense text (كشف screenshots), "
                         "plain TEXT_ for sparse text in a scene (a VIN stamped "
                         "on a door plate). Same price; worth running both.")
    ap.add_argument("--posts", type=Path,
                    default=Path("data/benchmark_moonshot_images.jsonl"),
                    help="JSONL of the posts Moonshot was benchmarked on, so "
                         "the three readers are compared on identical inputs")
    ap.add_argument("--dump", type=Path, default=Path("data/gvision_ocr.jsonl"))
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    cfg = load_config()
    key = api_key(cfg)
    if not key:
        sys.exit("No Vision key — set GOOGLE_VISION_API_KEY or "
                 "google_vision.api_key in config.yaml.")

    db.init()
    conn = db.connect()
    ids = [json.loads(l)["id"] for l in
           a.posts.read_text(encoding="utf-8").splitlines() if l.strip()][:a.limit]

    # Ground truth: identifiers the local vision pipeline read that are NOT in
    # the post text. Anything already in the text proves nothing about OCR.
    targets = {}
    for pid in ids:
        row = conn.execute("SELECT text FROM posts WHERE id=?", (pid,)).fetchone()
        txt = digits_of(row["text"] if row else "")
        known = [k[0] for k in conn.execute(
            "SELECT chassis_norm FROM vehicles WHERE post_id=? "
            "AND chassis_norm IS NOT NULL", (pid,))]
        img_only = [k for k in known if digits_of(k) and digits_of(k) not in txt]
        if img_only:
            targets[pid] = img_only

    media = {pid: [m[0] for m in conn.execute(
        "SELECT path FROM media WHERE post_id=?", (pid,))] for pid in ids}
    n_images = sum(len(v) for v in media.values())
    print(f"{len(ids)} posts, {n_images} images, {a.feature}\n"
          f"{sum(len(v) for v in targets.values())} image-derived chassis to "
          f"recover across {len(targets)} posts\n"
          f"est. cost ${n_images * 1.5 / 1000:.3f} (first 1,000/month free)\n",
          flush=True)

    results = {}

    def work(pid: int) -> None:
        chunks, errs = [], []
        for path in media[pid]:
            t, err = ocr_image(path, key, a.feature)
            if err:
                errs.append(err)
            if t:
                chunks.append(t)
        results[pid] = {"text": "\n".join(chunks), "errors": errs,
                        "images": len(media[pid])}
        print(f"  post {pid}: {len(media[pid])} images -> "
              f"{len(results[pid]['text'])} chars"
              + (f" | {len(errs)} errors: {errs[0][:60]}" if errs else ""),
              flush=True)

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        list(pool.map(work, ids))

    with open(a.dump, "w", encoding="utf-8") as fh:
        for pid, r in results.items():
            fh.write(json.dumps({"id": pid, "feature": a.feature, **r},
                                ensure_ascii=False) + "\n")

    exact = near = miss = 0
    misses = []
    for pid, wanted in targets.items():
        text = results.get(pid, {}).get("text", "")
        for w in wanted:
            hit = found_in(w, text)
            if hit == "exact":
                exact += 1
            elif hit == "near":
                near += 1
                misses.append(f"  ~ post {pid}: {w} present but one char out")
            else:
                miss += 1
                misses.append(f"  X post {pid}: {w} NOT in OCR text")

    total = exact + near + miss
    print(f"\n{'=' * 62}\nGOOGLE VISION OCR — {a.feature}\n{'=' * 62}")
    print(f"images OCR'd            : {n_images} in {time.time() - t0:.0f}s")
    print(f"image-derived chassis   : {total}")
    print(f"  recovered exactly     : {exact}  ({exact / total:.0%})" if total else "")
    print(f"  recovered one char out: {near}")
    print(f"  not found at all      : {miss}")
    for m in misses:
        print(m)
    print(f"\nOCR text saved to {a.dump} — feed it to the analyzer and the "
          f"grounding gate can finally see image-derived identifiers.")


if __name__ == "__main__":
    main()
