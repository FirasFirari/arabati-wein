import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db

db.init()
conn = db.connect()

rows = conn.execute("""
    SELECT CASE WHEN EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id)
                THEN 'from photos' ELSE 'from text' END AS origin,
           COUNT(v.id) AS vehicles,
           COUNT(DISTINCT p.id) AS posts
    FROM vehicles v JOIN posts p ON p.id = v.post_id
    GROUP BY origin""").fetchall()
print("registry provenance:")
for r in rows:
    print(f"  {r['origin']:12s} {r['vehicles']:6d} vehicles from {r['posts']} posts")

with_chassis = conn.execute("""
    SELECT CASE WHEN EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id)
                THEN 'from photos' ELSE 'from text' END AS origin,
           COUNT(*) FROM vehicles v JOIN posts p ON p.id = v.post_id
    WHERE v.chassis IS NOT NULL GROUP BY origin""").fetchall()
print("\nrecords carrying a chassis number:")
for r in with_chassis:
    print(f"  {r[0]:12s} {r[1]}")
