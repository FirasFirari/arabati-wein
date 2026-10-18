"""Remove registry rows that claim OUR OWN chassis but whose post never said it.

A model asked "is this the target?" sometimes answers by writing the target's
identifier into the vehicles array — the number is in its prompt, after all.
Three such rows were found carrying chassis <target tail>, one of them described as
"كورولا <target tail>", while the posts themselves list entirely different numbers.

These are the worst possible junk: searching the registry for the family's own
chassis returns them, so a REAL sighting would be indistinguishable from a
hallucination. A row is phantom when it carries the target identifier but the
source post's own text does not (checked with the same deterministic scanner
the free pass uses).

    python scripts\\purge_phantom_matches.py            # report only
    python scripts\\purge_phantom_matches.py --delete   # remove them
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from carhunt import db
from carhunt.config import load_config
from carhunt.prefilter import identifier_hit


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delete", action="store_true")
    ap.add_argument("--include-photo-posts", action="store_true",
                    help="also delete rows on posts that HAVE photos — only "
                         "after opening those photos yourself (see below)")
    args = ap.parse_args()

    car = load_config()["car"]
    conn = db.connect()
    rows = conn.execute("""
        SELECT v.id, v.chassis, v.plate, v.description, v.post_id,
               p.source, p.url, p.text,
               (SELECT COUNT(*) FROM media m WHERE m.post_id = p.id) nmedia
        FROM vehicles v JOIN posts p ON p.id = v.post_id""").fetchall()

    phantoms = []
    for r in rows:
        claimed = identifier_hit(r["chassis"] or "", car) or \
            identifier_hit(r["plate"] or "", car)
        if not claimed:
            continue
        if identifier_hit(r["text"] or "", car):
            continue        # the post really does carry it — a genuine hit
        phantoms.append(r)

    print(f"{len(rows)} registry rows scanned -> {len(phantoms)} phantom(s)\n")
    for r in phantoms:
        flag = f"  [{r['nmedia']} PHOTO(S) — VERIFY BY EYE]" if r["nmedia"] else ""
        print(f"  vehicle {r['id']} on post {r['post_id']} ({r['source']}){flag}")
        print(f"    chassis={r['chassis']!r} plate={r['plate']!r}")
        print(f"    {(r['description'] or '')[:100]}")
        print(f"    {r['url']}")

    if not phantoms:
        return
    if not args.delete:
        print("\n(report only — rerun with --delete to remove)")
        return

    # "The text does not contain it" proves nothing when the identifier could
    # be IN a photo — that is precisely the case worth finding. Rows on posts
    # with photos are held back until a human has actually looked.
    photo_rows = [r for r in phantoms if r["nmedia"]]
    doomed = phantoms if args.include_photo_posts else \
        [r for r in phantoms if not r["nmedia"]]
    if photo_rows and not args.include_photo_posts:
        print(f"\nHOLDING BACK {len(photo_rows)} row(s) on posts WITH photos — "
              f"open the images first, then rerun with --include-photo-posts")
    conn.executemany("DELETE FROM vehicles WHERE id = ?",
                     [(r["id"],) for r in doomed])
    conn.commit()
    print(f"deleted {len(doomed)} phantom row(s)")


if __name__ == "__main__":
    main()
