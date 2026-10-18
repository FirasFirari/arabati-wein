import os
import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db

db.init()
conn = db.connect()
rows = conn.execute(
    "SELECT p.id, m.path FROM posts p JOIN media m ON m.post_id = p.id "
    "WHERE p.id IN (507, 527, 539) ORDER BY p.id").fetchall()

MAGIC = {b"\xff\xd8\xff": "jpeg", b"\x89PNG": "png", b"GIF8": "gif",
         b"RIFF": "webp?", b"\x00\x00\x00": "mp4/iso?"}

for r in rows:
    path = r["path"]
    if not os.path.isfile(path):
        print(f"post {r['id']}: MISSING {path}")
        continue
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(12)
    kind = next((v for k, v in MAGIC.items() if head.startswith(k)), "unknown")
    print(f"post {r['id']}: {os.path.basename(path)[:45]:45s} "
          f"{size/1048576:6.2f} MB  ext={os.path.splitext(path)[1]:6s} magic={kind}")

# overall distribution
print("\n--- all media ---")
sizes = []
exts = {}
bad = 0
for (path,) in conn.execute("SELECT path FROM media"):
    if not os.path.isfile(path):
        bad += 1
        continue
    sizes.append(os.path.getsize(path))
    e = os.path.splitext(path)[1].lower()
    exts[e] = exts.get(e, 0) + 1
sizes.sort()
if sizes:
    print(f"count={len(sizes)} missing={bad}")
    print(f"median={sizes[len(sizes)//2]/1048576:.2f}MB  "
          f"p95={sizes[int(len(sizes)*0.95)]/1048576:.2f}MB  "
          f"max={sizes[-1]/1048576:.2f}MB")
    print(f"over 5MB: {sum(1 for s in sizes if s > 5*1048576)}")
print("extensions:", exts)
