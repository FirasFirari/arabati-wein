"""OCR every stored photo with Google Cloud Vision and keep the text.

Why this exists: the identifiers that matter most are stamped on compliance
plates and are absent from every post's text, so the grounding gate — the only
mechanical protection this project has against a model inventing a chassis
number — is switched off for the entire image half of the corpus. OCR text is
a verifiable artifact. Once it is stored beside the image, an identifier "read
off a photo" can be checked against something, and the gate applies again.

It also gives every already-processed photo a SECOND independent reader. That
is not theoretical: post 22337's compliance plate reads TGJX02P8G5046153 and
the registry held JTGJX02P8G0504615 — seven characters scrambled, sitting in a
registry other families search. Vision read it correctly. Nobody would ever
have found that without a second opinion.

PRIVACY. OCR text of a photographed registration card contains the owner's
name, address and phone number. This column is DATABASE-ONLY and must stay
that way — `export_site.py` does not read the media table, and it must not
start. Never publish `media.ocr_text`. See the privacy note in the project
memory: a stranger photographing someone's document is not that person
consenting to publication.

Resumable: only images with no OCR text yet are sent, so an interrupted run
costs nothing to restart. Cost is metered per image and the run stops at
--budget.

Usage:
    python scripts\\gvision_sweep.py --dry-run          # count and price it
    python scripts\\gvision_sweep.py --budget 40
    python scripts\\gvision_sweep.py --pending-only     # unscored posts first
"""
import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from carhunt import db
from carhunt.analyzer import sniff_media_type
from carhunt.config import load_config
from gvision_bench import api_key, ocr_image

PRICE_PER_IMAGE = 1.50 / 1000
FREE_TIER = 1000


def ensure_columns() -> None:
    """Additive migration. `ocr_text` NULL means 'not attempted yet', which is
    what makes the run resumable; empty string means 'attempted, no text
    found' — a real and common result for a photo of a car with no legible
    writing on it, and one we must not pay to rediscover."""
    conn = db.connect()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(media)")}
    with conn:
        if "ocr_text" not in cols:
            conn.execute("ALTER TABLE media ADD COLUMN ocr_text TEXT")
        if "ocr_at" not in cols:
            conn.execute("ALTER TABLE media ADD COLUMN ocr_at TEXT")
        if "ocr_error" not in cols:
            conn.execute("ALTER TABLE media ADD COLUMN ocr_error TEXT")
    # post_id lookups are the hot path for every consumer of this data and
    # media has no index on it; the same omission on `vehicles` turned a
    # benchmark query into a 15-minute full scan.
    with conn:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_post ON media(post_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_media_ocr ON media(ocr_at)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=40.0,
                    help="USD ceiling; the run stops when it is reached")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0, help="0 = every image")
    ap.add_argument("--pending-only", action="store_true",
                    help="only photos on posts that are still unscored")
    ap.add_argument("--feature", default="DOCUMENT_TEXT_DETECTION",
                    choices=("DOCUMENT_TEXT_DETECTION", "TEXT_DETECTION"))
    ap.add_argument("--dry-run", action="store_true",
                    help="count and price the work, send nothing")
    ap.add_argument("--retry-errors", action="store_true",
                    help="re-send only the images that previously errored. "
                         "Needed because a failure still stamps ocr_at, so the "
                         "normal resume (ocr_at IS NULL) skips them forever — "
                         "a single transient ConnectionResetError would "
                         "otherwise drop that photo from the corpus silently.")
    a = ap.parse_args()

    cfg = load_config()
    key = api_key(cfg)
    if not key:
        sys.exit("No Vision key — set google_vision.api_key in config.yaml.")

    db.init()
    ensure_columns()
    conn = db.connect()

    sql = ("SELECT m.id, m.path FROM media m JOIN posts p ON p.id = m.post_id "
           "WHERE " + ("m.ocr_error IS NOT NULL" if a.retry_errors
                       else "m.ocr_at IS NULL"))
    if a.pending_only:
        sql += " AND p.score IS NULL"
    sql += " ORDER BY m.id DESC"
    todo = conn.execute(sql).fetchall()
    if a.limit:
        todo = todo[:a.limit]

    # De-duplicate by path: the same file is referenced by more than one media
    # row and Vision bills per call, not per row. Rows sharing a path are
    # filled in from the one call.
    by_path: dict[str, list[int]] = {}
    for r in todo:
        by_path.setdefault(r["path"], []).append(r["id"])

    n_calls = len(by_path)
    est = max(n_calls - FREE_TIER, 0) * PRICE_PER_IMAGE
    print(f"{len(todo)} media rows without OCR -> {n_calls} distinct files\n"
          f"estimated cost ${est:.2f} (first {FREE_TIER}/month free) | "
          f"budget ${a.budget:.2f} | {a.feature}", flush=True)
    if a.dry_run:
        print("dry run — nothing sent.")
        return
    if est > a.budget:
        print(f"WARNING: estimate exceeds budget; the run will stop at "
              f"${a.budget:.2f} and can be resumed.", flush=True)

    lock = threading.Lock()
    state = {"calls": 0, "chars": 0, "errors": 0, "empty": 0, "spent": 0.0,
             "stop": False}
    t0 = time.time()

    def work(item) -> None:
        path, row_ids = item
        with lock:
            if state["stop"]:
                return
        text, err = ocr_image(path, key, a.feature)
        # A billing/quota 403 is transient right after enabling billing — it
        # propagates across Google's fleet over several minutes — so it gets
        # one retry rather than burning the image for the whole run.
        if err and ("403" in err or "Connection" in err or "Timeout" in err):
            time.sleep(3)
            text, err = ocr_image(path, key, a.feature)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn2 = db.connect()
        with conn2:
            for rid in row_ids:
                conn2.execute(
                    "UPDATE media SET ocr_text=?, ocr_at=?, ocr_error=? WHERE id=?",
                    (text or "", now, err, rid))
        with lock:
            state["calls"] += 1
            state["chars"] += len(text or "")
            if err:
                state["errors"] += 1
                # A file rejected by the magic-byte sniff never reached the
                # API and costs nothing; a network failure did reach it and
                # may well have been billed, but we cannot tell, so it is not
                # counted and the real bill may be a few cents higher.
            else:
                if not (text or "").strip():
                    state["empty"] += 1
                # Billed per IMAGE SENT, not per image that happened to
                # contain text. Counting only non-blank results undercounted
                # an 85-minute run by $10 — 7,986 photos of cars with nothing
                # written on them are still 7,986 billable units.
                state["spent"] += PRICE_PER_IMAGE
            if state["spent"] >= a.budget:
                state["stop"] = True
            n = state["calls"]
            if n % 250 == 0:
                rate = n / max(time.time() - t0, 1)
                left = (n_calls - n) / max(rate, 0.01) / 60
                print(f"  {n}/{n_calls} | ${state['spent']:.2f} | "
                      f"{state['errors']} errors | {state['empty']} blank | "
                      f"{rate:.1f} img/s | ~{left:.0f} min left", flush=True)

    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        list(pool.map(work, by_path.items()))

    el = time.time() - t0
    print(f"\nOCR'd {state['calls']} files in {el/60:.1f} min | "
          f"${state['spent']:.2f} spent")
    print(f"  with text : {state['calls'] - state['empty'] - state['errors']}")
    print(f"  blank     : {state['empty']}  (no legible writing in the photo)")
    print(f"  errors    : {state['errors']}")
    print(f"  characters: {state['chars']:,}")
    if state["stop"]:
        print("  [BUDGET REACHED — rerun to continue; already-OCR'd files are "
              "skipped]")


if __name__ == "__main__":
    main()
