import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db

db.init()
c = db.connect()
rows = c.execute(
    "SELECT id, url, LENGTH(text) AS n, substr(text,1,180) AS t FROM posts "
    "WHERE source='facebook:993842458658985' ORDER BY id DESC LIMIT 8").fetchall()
print(f"newest {len(rows)} posts from the feed harvest:\n")
for r in rows:
    has_link = "link" if r["url"] else "NO LINK"
    print(f"--- post {r['id']} ({r['n']} chars, {has_link})")
    print("   ", (r["t"] or "").replace("\n", " | ")[:170])

print("\ntotals for this group:", c.execute(
    "SELECT COUNT(*) FROM posts WHERE source='facebook:993842458658985'").fetchone()[0])
print("unscored overall:", c.execute(
    "SELECT COUNT(*) FROM posts WHERE score IS NULL").fetchone()[0])
