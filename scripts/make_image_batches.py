"""Build vision batches from unscored posts that have images.

The image half of the queue is the high-yield half: the text corpus is
saturated at ~2% new-car yield, while photographed compliance plates keep
producing full 17-character VINs that appear nowhere in any post's text.

Batch numbers continue from the highest existing imgbatch_NNN.json so a new
run can never reuse a number — ingest_pending.py pairs verdicts_NNN.json with
imgbatch_NNN.json, so a collision would silently ingest a verdict against the
wrong batch.

Usage:
    python scripts/make_image_batches.py [--size 15] [--out data/image_queue]
"""
import argparse
import glob
import json
import pathlib
import re
import sqlite3


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=15)
    ap.add_argument("--out", type=pathlib.Path, default=pathlib.Path("data/image_queue"))
    ap.add_argument("--db", default="data/carhunt.db")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    nums, inflight = [], set()
    for f in glob.glob(str(a.out / "imgbatch_*.json")):
        if m := re.search(r"imgbatch_(\d+)\.json$", f):
            nums.append(int(m.group(1)))
        # Post ids already handed to an agent. A post stays unscored until its
        # verdict is ingested, so without this a batch built while an agent is
        # still working re-issues the very posts it is holding - the
        # double-dispatch that wasted two agents' work on 2026-08-24.
        try:
            for post in json.loads(pathlib.Path(f).read_text(encoding="utf-8")):
                if post.get("id"):
                    inflight.add(int(post["id"]))
        except Exception:
            pass
    start = max(nums, default=0) + 1

    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT p.id, p.source, p.url, p.text
          FROM posts p
         WHERE p.score IS NULL
           AND EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id)
         ORDER BY p.id
    """).fetchall()

    posts = []
    for r in rows:
        imgs = [m["path"] for m in conn.execute(
            "SELECT path FROM media WHERE post_id = ? ORDER BY id", (r["id"],))]
        imgs = [p for p in imgs if pathlib.Path(p).exists()]
        if imgs and r["id"] not in inflight:
            posts.append({"id": r["id"], "source": r["source"], "url": r["url"],
                          "text": r["text"], "images": imgs})

    written = 0
    for i in range(0, len(posts), a.size):
        chunk = posts[i:i + a.size]
        f = a.out / f"imgbatch_{start + written:03d}.json"
        f.write_text(json.dumps(chunk, ensure_ascii=False, indent=1), encoding="utf-8")
        written += 1

    if inflight:
        print(f"already batched  : {len(inflight)} (skipped - may be in flight)")
    print(f"posts with images : {len(posts)}")
    print(f"images total      : {sum(len(p['images']) for p in posts)}")
    print(f"batches written   : {written}"
          f" (imgbatch_{start:03d} .. imgbatch_{start + written - 1:03d})"
          if written else "batches written   : 0")


if __name__ == "__main__":
    main()
