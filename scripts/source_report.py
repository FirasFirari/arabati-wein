import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db

db.init()
conn = db.connect()
print("source | posts | analyzed | vehicles | photos | top_score")
for row in conn.execute("""
    SELECT p.source,
           COUNT(DISTINCT p.id),
           COUNT(DISTINCT CASE WHEN p.score IS NOT NULL THEN p.id END),
           COUNT(DISTINCT v.id),
           COUNT(DISTINCT m.id),
           MAX(p.score)
    FROM posts p
    LEFT JOIN vehicles v ON v.post_id = p.id
    LEFT JOIN media m ON m.post_id = p.id
    GROUP BY p.source ORDER BY 2 DESC"""):
    print(" | ".join(str(x) for x in row))
print("\nTOTALS:")
print("posts:", conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0])
print("vehicles:", conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0])
print("media:", conn.execute("SELECT COUNT(*) FROM media").fetchone()[0])
