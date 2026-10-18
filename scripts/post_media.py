import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db

db.init()
conn = db.connect()
for pid in [int(a) for a in sys.argv[1:]]:
    row = conn.execute("SELECT id, source, url, text, score FROM posts WHERE id = ?", (pid,)).fetchone()
    if not row:
        print(f"post {pid}: not found")
        continue
    print(f"\n=== post {row['id']} score={row['score']} [{row['source']}]")
    print("url:", row["url"])
    print("text:", (row["text"] or "")[:400])
    for m in conn.execute("SELECT path FROM media WHERE post_id = ?", (pid,)):
        print("PHOTO:", m["path"])
