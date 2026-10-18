import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db

db.init()
c = db.connect()

print("posts scored by the offline/text-only tiers that HAVE photos")
print("(a text heuristic cannot see them, and they are now marked scored):\n")
rows = c.execute("""
    SELECT json_extract(p.verdict,'$.tier') AS tier,
           COUNT(*) AS n,
           SUM(CASE WHEN json_extract(p.verdict,'$.needs_visual_review') THEN 1 ELSE 0 END) AS flagged
    FROM posts p
    WHERE json_extract(p.verdict,'$.tier') IN
          ('offline_text_heuristic','prefiltered_offline')
      AND EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id)
    GROUP BY tier""").fetchall()
for r in rows:
    print(f"   {r['tier']:24s} with photos: {r['n']:4d}   "
          f"self-flagged for visual review: {r['flagged']}")

total = c.execute("""
    SELECT COUNT(*) FROM posts p
    WHERE json_extract(p.verdict,'$.tier') IN
          ('offline_text_heuristic','prefiltered_offline')
      AND EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id)""").fetchone()[0]
print(f"\ntotal photo posts never visually analysed: {total}")

print("\ntop offline text scores (would have escalated in our pipeline):")
for r in c.execute("""
    SELECT id, score, substr(json_extract(verdict,'$.reasons'),1,110) AS why
    FROM posts WHERE json_extract(verdict,'$.tier')='offline_text_heuristic'
    ORDER BY score DESC LIMIT 5"""):
    print(f"   post {r['id']} score={r['score']}: {r['why']}")
