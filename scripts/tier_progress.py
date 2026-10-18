import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db

db.init()
c = db.connect()
print("verdicts by tier:")
for r in c.execute(
        "SELECT COALESCE(json_extract(verdict,'$.tier'),'(none)') t, COUNT(*) n "
        "FROM posts WHERE verdict IS NOT NULL GROUP BY t ORDER BY n DESC"):
    print(f"   {r['t']:20s} {r['n']}")

print("\nunscored remaining:", c.execute(
    "SELECT COUNT(*) FROM posts WHERE score IS NULL").fetchone()[0])

print("\nhighest scores now (deep-verified tiers only):")
for r in c.execute(
        "SELECT id, score, json_extract(verdict,'$.tier') t, url, "
        "substr(json_extract(verdict,'$.reasons'),1,160) reasons "
        "FROM posts WHERE score >= 60 AND json_extract(verdict,'$.tier') IN "
        "('deep','deep_after_batch','deep_recheck','identifier_scan') "
        "ORDER BY score DESC LIMIT 10"):
    print(f"   post {r['id']} score={r['score']} [{r['t']}]")
    print(f"      {r['reasons']}")
    print(f"      {r['url']}")

print("\nscreen-tier hits still awaiting verification:", c.execute(
    "SELECT COUNT(*) FROM posts WHERE score >= 40 AND "
    "json_extract(verdict,'$.tier') IN ('batch_screen','screen')").fetchone()[0])
