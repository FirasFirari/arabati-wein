"""Remove media rows/files whose contents are not real images.

Facebook CDN links that expired returned HTML login pages, which were saved
with a .jpg extension. They are useless and cause API errors.
"""
import os
import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db
from carhunt.analyzer import sniff_media_type

db.init()
conn = db.connect()
rows = conn.execute("SELECT id, post_id, path FROM media").fetchall()
bad_ids, bad_paths, affected_posts = [], [], set()
for r in rows:
    path = r["path"]
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        bad_ids.append(r["id"])
        affected_posts.add(r["post_id"])
        continue
    if sniff_media_type(head) is None:
        bad_ids.append(r["id"])
        bad_paths.append(path)
        affected_posts.add(r["post_id"])

print(f"{len(rows)} media rows, {len(bad_ids)} not real images")
if bad_ids:
    conn.executemany("DELETE FROM media WHERE id = ?", [(i,) for i in bad_ids])
    conn.commit()
    removed = 0
    for p in bad_paths:
        try:
            os.remove(p)
            removed += 1
        except OSError:
            pass
    print(f"deleted {len(bad_ids)} rows and {removed} files "
          f"across {len(affected_posts)} posts")

# posts whose analysis failed on those images should be scored again
requeued = conn.execute(
    "UPDATE posts SET score = NULL, verdict = NULL "
    "WHERE verdict LIKE '%Could not process image%'").rowcount
conn.commit()
print(f"requeued {requeued} posts that failed on bad images")
