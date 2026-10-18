"""Re-verify every high-scoring screen-tier verdict with the full model.

Batch requests only run the cheap screen tier, which has been observed
inventing VIN-plate detail. Anything it scored highly must be confirmed by
the deep model (with the owner's reference photos) before it is believed.
"""
import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db
from carhunt.analyzer import Analyzer
from carhunt.config import load_config

cfg = load_config()
db.init()
conn = db.connect()
threshold = int(sys.argv[1]) if len(sys.argv) > 1 else 40

rows = conn.execute(
    "SELECT id, score FROM posts WHERE score >= ? "
    "AND json_extract(verdict,'$.tier') IN ('batch_screen','screen') "
    "ORDER BY score DESC", (threshold,)).fetchall()
print(f"{len(rows)} screen-tier verdicts at score >= {threshold} to re-verify\n")

analyzer = Analyzer(cfg)
confirmed = cleared = failed = 0
for row in rows:
    post = db.post_with_media(row["id"])
    if not post:
        continue
    verdict = analyzer.analyze(post["text"], post["media"])
    verdict["tier"] = "deep_recheck"
    db.save_analysis(row["id"], verdict)
    new = verdict.get("score")
    if new is None:
        # an API/parse failure is NOT a clearance — the post stays unverified
        failed += 1
        print(f"  UNVERIFIED post {row['id']}: re-check failed "
              f"({verdict.get('error','')[:120]})")
        continue
    if new >= cfg["matching"]["alert_threshold"]:
        confirmed += 1
        print(f"  CONFIRMED post {row['id']}: {row['score']} -> {new}")
        print(f"    {verdict.get('reasons','')[:400]}")
        print(f"    {post['url']}")
    else:
        cleared += 1
        print(f"  cleared post {row['id']}: {row['score']} -> {new}")
print(f"\n{confirmed} confirmed, {cleared} cleared, {failed} could not be verified")
if failed:
    print("Unverified posts stay queued — rerun when the API is available.")
