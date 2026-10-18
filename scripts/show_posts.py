import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db

ids = [int(a) for a in sys.argv[1:]] or [214, 217, 218, 219]
db.init()
conn = db.connect()
for pid in ids:
    row = conn.execute("SELECT id, source, url, text FROM posts WHERE id = ?", (pid,)).fetchone()
    if not row:
        continue
    print(f"\n{'=' * 70}\npost {row['id']} [{row['source']}] {row['url'] or ''}")
    print((row["text"] or "")[:2600])
