import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db

db.init()
conn = db.connect()

print("per source: posts / scored / posts that produced vehicles / vehicles")
for r in conn.execute("""
    SELECT p.source,
           COUNT(DISTINCT p.id) AS posts,
           COUNT(DISTINCT CASE WHEN p.score IS NOT NULL THEN p.id END) AS scored,
           COUNT(DISTINCT v.post_id) AS posts_with_vehicles,
           COUNT(v.id) AS vehicles
    FROM posts p LEFT JOIN vehicles v ON v.post_id = p.id
    GROUP BY p.source ORDER BY vehicles DESC"""):
    print(f"  {r['source']:32s} {r['posts']:5d} / {r['scored']:5d} / "
          f"{r['posts_with_vehicles']:4d} / {r['vehicles']:6d}")

# does a live-monitored channel actually contribute vehicles?
print("\nmost recent vehicles added from a Telegram monitor source:")
for r in conn.execute("""
    SELECT v.extracted_at, p.source, v.make, v.model, v.chassis
    FROM vehicles v JOIN posts p ON p.id = v.post_id
    WHERE p.source LIKE 'telegram:%'
    ORDER BY v.id DESC LIMIT 5"""):
    print(f"  {r['extracted_at'][:19]}  {r['source']:24s} "
          f"{(r['make'] or '')} {(r['model'] or '')} {r['chassis'] or ''}")

waiting = conn.execute(
    "SELECT COUNT(*) FROM posts WHERE score IS NULL").fetchone()[0]
print(f"\nposts holding vehicles hostage until scoring runs: {waiting}")
