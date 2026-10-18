import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db

db.init()
c = db.connect()
print("unscored queue:", c.execute("SELECT COUNT(*) FROM posts WHERE score IS NULL").fetchone()[0])
print("\nverdicts by tier (how each post was settled):")
for row in c.execute(
        "SELECT COALESCE(json_extract(verdict,'$.tier'),'(pre-tier)') AS t, COUNT(*) "
        "FROM posts WHERE verdict IS NOT NULL GROUP BY t ORDER BY 2 DESC"):
    print(f"  {row[0]:18s} {row[1]}")
