import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from carhunt import db

db.init()
c = db.connect()
print("posts:", c.execute("SELECT COUNT(*) FROM posts").fetchone()[0])
print("analyzed:", c.execute("SELECT COUNT(*) FROM posts WHERE score IS NOT NULL").fetchone()[0])
print("vehicles:", c.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0])
print("media files:", c.execute("SELECT COUNT(*) FROM media").fetchone()[0])
print("top score:", c.execute("SELECT MAX(score) FROM posts").fetchone()[0])
print("by source:")
for row in c.execute("SELECT source, COUNT(*) FROM posts GROUP BY 1 ORDER BY 2 DESC LIMIT 10"):
    print("  ", row[0], row[1])
