import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db

db.init()
c = db.connect()
rows = c.execute(
    "SELECT id, url, LENGTH(text) AS n, substr(text,1,150) AS t FROM posts "
    "WHERE id > 9800 ORDER BY id DESC LIMIT 10").fetchall()

with_link = c.execute(
    "SELECT COUNT(*) FROM posts WHERE id > 9800 AND url IS NOT NULL").fetchone()[0]
total = c.execute("SELECT COUNT(*) FROM posts WHERE id > 9800").fetchone()[0]
distinct = c.execute(
    "SELECT COUNT(DISTINCT url) FROM posts WHERE id > 9800 AND url IS NOT NULL").fetchone()[0]
print(f"recent harvest: {total} posts, {with_link} with a link, "
      f"{distinct} distinct URLs")
print("(distinct should equal with_link — equal means no key collisions)\n")

truncated = c.execute(
    "SELECT COUNT(*) FROM posts WHERE id > 9800 AND text LIKE '%See more%'").fetchone()[0]
print(f"still truncated at 'See more': {truncated}/{total}\n")

for r in rows:
    print(f"--- post {r['id']} ({r['n']} chars)")
    print(f"    {r['url']}")
    print(f"    {(r['t'] or '').replace(chr(10), ' | ')[:140]}")
