"""Remove vehicle rows left behind by the append-on-replay bug.

For each post, keep only the rows matching its CURRENT verdict: drop exact
duplicates, then trim any surplus beyond what the verdict claims (oldest
first, since the newest rows came from the most recent analysis).
"""
import json
import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db

db.init()
conn = db.connect()
before = conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]

# 1. exact duplicates within a post — keep the newest row of each group
removed_dupes = conn.execute("""
    DELETE FROM vehicles WHERE id NOT IN (
        SELECT MAX(id) FROM vehicles
        GROUP BY post_id, COALESCE(chassis,''), COALESCE(model,''),
                 COALESCE(make,''), COALESCE(description,'')
    )""").rowcount
conn.commit()

# 2. surplus beyond the verdict's claim — keep the newest N
removed_surplus = 0
for row in conn.execute(
        "SELECT p.id, p.verdict, COUNT(v.id) AS n FROM posts p "
        "JOIN vehicles v ON v.post_id = p.id "
        "WHERE p.verdict IS NOT NULL GROUP BY p.id HAVING n > 0").fetchall():
    try:
        claimed = len(json.loads(row["verdict"]).get("vehicles") or [])
    except (json.JSONDecodeError, AttributeError):
        continue
    if row["n"] <= claimed:
        continue
    cur = conn.execute(
        "DELETE FROM vehicles WHERE id IN ("
        "  SELECT id FROM vehicles WHERE post_id = ? ORDER BY id ASC LIMIT ?)",
        (row["id"], row["n"] - claimed))
    removed_surplus += cur.rowcount
conn.commit()

after = conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]
print(f"vehicles: {before} -> {after}")
print(f"  exact duplicates removed: {removed_dupes}")
print(f"  surplus rows removed:     {removed_surplus}")
