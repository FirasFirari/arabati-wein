import json
import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db

db.init()
conn = db.connect()

total = conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]
print("vehicle rows:", total)

# rows stored beyond what each post's CURRENT verdict claims
excess_posts = excess_rows = 0
for row in conn.execute(
        "SELECT p.id, p.verdict, COUNT(v.id) AS n FROM posts p "
        "JOIN vehicles v ON v.post_id = p.id "
        "WHERE p.verdict IS NOT NULL GROUP BY p.id"):
    try:
        claimed = len(json.loads(row["verdict"]).get("vehicles") or [])
    except (json.JSONDecodeError, AttributeError):
        continue
    if row["n"] > claimed:
        excess_posts += 1
        excess_rows += row["n"] - claimed
print(f"posts with more rows than their verdict claims: {excess_posts} "
      f"({excess_rows} excess rows)")

# exact duplicate rows within the same post
dupes = conn.execute(
    "SELECT COUNT(*) FROM (SELECT post_id, COALESCE(chassis,''), COALESCE(model,''), "
    "COALESCE(description,''), COUNT(*) c FROM vehicles "
    "GROUP BY 1,2,3,4 HAVING c > 1)").fetchone()[0]
extra = conn.execute(
    "SELECT COALESCE(SUM(c - 1), 0) FROM (SELECT post_id, COALESCE(chassis,''), "
    "COALESCE(model,''), COALESCE(description,''), COUNT(*) c FROM vehicles "
    "GROUP BY 1,2,3,4 HAVING c > 1)").fetchone()[0]
print(f"duplicate groups within a post: {dupes} ({extra} redundant rows)")

pending = conn.execute(
    "SELECT COUNT(*) FROM posts WHERE extracted = 0 AND verdict IS NOT NULL "
    "AND json_extract(verdict, '$.vehicles') IS NOT NULL").fetchone()[0]
print("posts with a valid vehicles list still marked unextracted:", pending)
