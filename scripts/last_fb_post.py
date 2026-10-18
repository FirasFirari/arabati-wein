import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db

db.init()
c = db.connect()
r = c.execute(
    "SELECT id, url, text FROM posts WHERE source = 'facebook:993842458658985' "
    "ORDER BY id DESC LIMIT 1").fetchone()
if not r:
    print("no posts")
else:
    print("id:", r["id"])
    print("url:", r["url"])
    print("text:")
    print((r["text"] or "")[:600])
