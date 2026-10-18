import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db

db.init()
c = db.connect()
src = "facebook:945879980039956"
print("count:", c.execute("SELECT COUNT(*) FROM posts WHERE source = ?", (src,)).fetchone()[0])
print("with media:", c.execute(
    "SELECT COUNT(DISTINCT p.id) FROM posts p JOIN media m ON m.post_id = p.id WHERE p.source = ?",
    (src,)).fetchone()[0])
for row in c.execute(
        "SELECT id, substr(text, 1, 250), url FROM posts WHERE source = ? ORDER BY id DESC LIMIT 3", (src,)):
    print("---", row[0])
    print(row[1])
    print(row[2])
