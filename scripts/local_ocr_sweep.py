"""FREE photo OCR on this PC — replaces Google Vision while that project is
billing-suspended. Run with the OCR venv, not the main interpreter:

    .venv-ocr\\Scripts\\python scripts\\local_ocr_sweep.py --hours 6
    .venv-ocr\\Scripts\\python scripts\\local_ocr_sweep.py --limit 50     # trial

ENGINE (benchmarked 2026-10-07 by scripts/ocr_benchmark.py against Vision's
own reads of 60 photos): RapidOCR, Arabic PP-OCRv5 recogniser + the
multilingual v3 DETECTOR. Recovers ~60% of the identifiers Vision found on
Arabic-digit list photos and ~41% on Latin-digit photos (~51% counting a
correct last-6 tail). The detector is the whole story: the default and v5
detectors box the Arabic words and skip the ٠-٩ number at the left of each
list line — 0/15 on a clean list photo, against 11/15 with this one.
So: clearly weaker than Vision, clearly better than the nothing these ~140k
photos have had since 2026-09-01.

Writes media.ocr_text / ocr_at / ocr_error like gvision_sweep.py, plus
media.ocr_engine so a later Vision pass can tell these rows apart and redo
them. Identical files (cross-posts) are read once and written to every row.

Built to be a polite background job on a PC that runs short of RAM:
  * below-normal CPU priority, 4 inference threads (of 20)
  * stops at once when data/PAUSED or data/PAUSED_LIGHT appears
  * waits while free RAM is under --min-free-gb, and gives up after 10 min
  * newest posts' photos first
"""
import argparse
import datetime
import sqlite3
import sys
import time
from pathlib import Path

import psutil
from rapidocr import LangDet, LangRec, ModelType, OCRVersion, RapidOCR

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB = DATA / "carhunt.db"
LOG = DATA / "logs" / "local_ocr.log"
ENGINE_TAG = "rapidocr-arabic-v5+multidet-v3"


def log(msg: str) -> None:
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def paused() -> bool:
    return (DATA / "PAUSED").exists() or (DATA / "PAUSED_LIGHT").exists()


def free_gb() -> float:
    return psutil.virtual_memory().available / 2**30


def make_engine(threads: int) -> RapidOCR:
    return RapidOCR(params={
        "Det.ocr_version": OCRVersion.PPOCRV4,   # the registry files v3-multi here
        "Det.model_type": ModelType.MOBILE,
        "Det.lang_type": LangDet.MULTI,
        "Rec.lang_type": LangRec.ARABIC,
        "Rec.ocr_version": OCRVersion.PPOCRV5,
        "Rec.model_type": ModelType.MOBILE,
        "EngineConfig.onnxruntime.intra_op_num_threads": threads,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
    })


def ensure_column(conn) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(media)")}
    if "ocr_engine" not in cols:
        conn.execute("ALTER TABLE media ADD COLUMN ocr_engine TEXT")
        conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=0, help="stop after this long (0 = no limit)")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many files")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--min-free-gb", type=float, default=1.5)
    args = ap.parse_args()

    if paused():
        log("paused (data/PAUSED*) — nothing done")
        return
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)

    conn = sqlite3.connect(DB, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    ensure_column(conn)
    # newest posts first; one entry per FILE, carrying every row that shares it
    rows = conn.execute(
        "SELECT m.path, group_concat(m.id) FROM media m WHERE m.ocr_at IS NULL "
        "GROUP BY m.path ORDER BY max(m.post_id) DESC").fetchall()
    log(f"start: {len(rows)} unread files, {free_gb():.1f} GB RAM free, "
        f"{args.threads} threads, engine {ENGINE_TAG}")

    engine = make_engine(args.threads)
    deadline = time.time() + args.hours * 3600 if args.hours else None
    done = errors = empty = chars = 0
    t0 = time.time()
    for path, ids in rows:
        if paused():
            log("pause flag seen — stopping")
            break
        if deadline and time.time() > deadline:
            log("time limit reached")
            break
        if args.limit and done >= args.limit:
            break
        waited = 0
        while free_gb() < args.min_free_gb:
            if waited >= 600:
                log(f"RAM stayed under {args.min_free_gb} GB for 10 min — giving up")
                rows = []
                break
            time.sleep(30)
            waited += 30
        if not rows:
            break

        text, err = "", None
        if not Path(path).exists():
            err = "file missing"
        else:
            try:
                res = engine(path)
                text = "\n".join(res.txts) if res is not None and res.txts else ""
            except Exception as e:  # corrupt / HTML-saved-as-jpg / decode errors
                err = f"{type(e).__name__}: {e}"[:300]
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.executemany(
            "UPDATE media SET ocr_text=?, ocr_at=?, ocr_error=?, ocr_engine=? WHERE id=?",
            [(text, now, err, ENGINE_TAG, int(i)) for i in ids.split(",")])
        done += 1
        errors += bool(err)
        empty += (not err and not text.strip())
        chars += len(text)
        if done % 25 == 0:
            conn.commit()
        if done % 500 == 0:
            rate = done / (time.time() - t0)
            log(f"  {done} files, {rate:.2f}/s, {errors} errors, {empty} blank, "
                f"{free_gb():.1f} GB free")
    conn.commit()
    secs = time.time() - t0
    log(f"done: {done} files in {secs / 60:.1f} min "
        f"({done / max(secs, 1):.2f}/s), {errors} errors, {empty} blank, "
        f"{chars:,} chars; {len(rows) - done if rows else '?'} files left")


if __name__ == "__main__":
    main()
