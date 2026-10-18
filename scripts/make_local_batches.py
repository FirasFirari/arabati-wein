"""Chunk the unscored backlog into work units sized for a subagent pass.

Sizing is driven by expected OUTPUT, not input: a numbered كشف/حصر list post
of 10k characters can yield 240 vehicle objects, so chunks are capped on
chassis-like numbers as well as characters. Posts the free gates would settle
are excluded — run `python -m carhunt.free_pass` first.

Usage:
    python scripts/make_local_batches.py [--outdir DIR] [--chars 20000] [--nums 120]
"""
import argparse
import json
import pathlib
import re
import sys
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from carhunt import db
from carhunt.config import load_config
from carhunt.prefilter import identifier_hit, is_relevant

# This script prints Arabic — post text, plate values like '12345 خ1', the
# identifiers the grounding gate nulls. The Windows console is cp1252 and
# raises UnicodeEncodeError on the first one. Here that is not cosmetic: the
# crash lands INSIDE the ingest loop, after some posts have already been
# written, leaving a half-ingested batch. It did exactly that on wave 1 —
# batch 021 stopped at 64 of 121 posts.
import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
LONG_NUM = re.compile(r"\b\d{5,17}\b")


def weight(text: str) -> int:
    """How many chassis-like numbers this post is likely to yield."""
    t = unicodedata.normalize("NFKC", text or "").translate(AR_DIGITS)
    return len(LONG_NUM.findall(t))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", type=pathlib.Path, default=pathlib.Path("data/local_batches"))
    ap.add_argument("--chars", type=int, default=20000)
    ap.add_argument("--nums", type=int, default=120)
    ap.add_argument("--no-media", action="store_true",
                    help="skip posts that have images - they belong to the "
                         "vision pass (scripts/make_image_batches.py). Without "
                         "this the same post is analysed twice, once blind.")
    a = ap.parse_args()

    cfg = load_config()
    car = cfg["car"]
    general = [s.lower() for s in (cfg.get("general_sources") or [])]
    db.init()
    conn = db.connect()
    rows = conn.execute(
        "SELECT p.id, p.source, p.url, p.text, "
        "(SELECT COUNT(*) FROM media m WHERE m.post_id = p.id) AS nmedia "
        "FROM posts p WHERE p.score IS NULL ORDER BY p.id").fetchall()

    # Google Vision OCR of this post's photos, keyed by post. Fetched in ONE
    # pass — media now has an index on post_id, but a per-post query inside the
    # loop is still the shape that has twice stalled this project.
    ocr_by_post: dict[int, str] = {}
    for r in conn.execute(
            "SELECT post_id, ocr_text FROM media "
            "WHERE ocr_text IS NOT NULL AND ocr_text <> ''"):
        ocr_by_post.setdefault(r[0], []).append(r[1])
    ocr_by_post = {k: chr(10).join(v) for k, v in ocr_by_post.items()}

    todo, free = [], 0
    for r in rows:
        if identifier_hit(r["text"], car):
            continue  # free_pass handles these as alerts
        strict = any(g in (r["source"] or "").lower() for g in general)
        if not is_relevant(r["text"], ["x"] * r["nmedia"], strict=strict):
            free += 1
            continue
        if a.no_media and r["nmedia"]:
            continue  # handled by the vision pass
        todo.append(r)

    a.outdir.mkdir(parents=True, exist_ok=True)
    # NOTE: this deletes and renumbers every batch from 001 on each run. A
    # verdict file written against an earlier generation will no longer line up
    # with the batch of the same number, and ingest_local_verdicts.py will
    # (correctly) refuse it with COVERAGE FAILURE. That is the whole of the
    # "something outside this session re-chunks my batches" report agents keep
    # filing - it is this loop, not a hostile process. Ingest before you
    # regenerate.
    for old in a.outdir.glob("batch_*.json"):
        old.unlink()

    batches, cur, c_chars, c_nums = [], [], 0, 0
    for r in todo:
        ocr = ocr_by_post.get(r["id"], "")
        # Size on text AND OCR together. A photo of a كشف screenshot can carry
        # more chassis numbers than the post body does, and the binding
        # constraint on a subagent is how much it has to WRITE.
        combined = (r["text"] or "") + chr(10) + ocr
        w, n = len(combined), weight(combined)
        if cur and (c_chars + w > a.chars or c_nums + n > a.nums):
            batches.append(cur)
            cur, c_chars, c_nums = [], 0, 0
        entry = {"id": r["id"], "source": r["source"], "url": r["url"],
                 "text": r["text"]}
        if ocr:
            entry["ocr_text"] = ocr
        cur.append(entry)
        c_chars += w
        c_nums += n
    if cur:
        batches.append(cur)

    for i, b in enumerate(batches, 1):
        p = a.outdir / f"batch_{i:03d}.json"
        p.write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")

    with_ocr = sum(1 for b in batches for e in b if e.get("ocr_text"))
    print(f"{len(rows)} unscored; {free} settle free; {len(todo)} need a model")
    print(f"{with_ocr} of them carry Google Vision OCR text from their photos")
    print(f"wrote {len(batches)} batches to {a.outdir}")
    sizes = [len(b) for b in batches]
    print(f"posts per batch: min {min(sizes)} / median {sorted(sizes)[len(sizes)//2]} / max {max(sizes)}")


if __name__ == "__main__":
    main()
