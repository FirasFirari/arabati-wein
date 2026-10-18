import json
import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db

db.init()
c = db.connect()
for tier in ("offline_text_heuristic", "prefiltered_offline", "codex_manual_visual"):
    rows = c.execute(
        "SELECT id, source, score, verdict FROM posts "
        "WHERE json_extract(verdict,'$.tier') = ? LIMIT 2", (tier,)).fetchall()
    print(f"\n=== {tier} ===")
    for r in rows:
        v = json.loads(r["verdict"])
        print(f"  post {r['id']} [{r['source']}] score={r['score']}")
        print("   ", json.dumps(v, ensure_ascii=False)[:400])
    n = c.execute("SELECT COUNT(*) FROM posts WHERE json_extract(verdict,'$.tier') = ?",
                  (tier,)).fetchone()[0]
    hi = c.execute("SELECT COUNT(*) FROM posts WHERE json_extract(verdict,'$.tier') = ? "
                   "AND score >= 40", (tier,)).fetchone()[0]
    print(f"   total: {n}, of which score>=40: {hi}")
