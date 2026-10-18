"""Apply the alert cap to verdicts stored BEFORE it existed (2026-10-08).

    python scripts\\cap_unbacked_alerts.py            # list what would change
    python scripts\\cap_unbacked_alerts.py --apply    # write it

New verdicts are capped as they are written (`db.set_verdict`). This catches
the old ones: any score at or above `alert_threshold` with no identifier of the
target behind it drops to threshold - 1, and the model's number is kept in the
verdict as `model_score`. Safe to re-run; a capped row is below the line.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from carhunt import db
from carhunt.config import load_config
from carhunt.prefilter import cap_unbacked_alert


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the capped verdicts")
    a = ap.parse_args()
    cfg = load_config()
    threshold = cfg["matching"]["alert_threshold"]
    conn = db.connect()
    rows = conn.execute("SELECT id, verdict, text FROM posts WHERE score >= ? "
                        "ORDER BY score DESC", (threshold,)).fetchall()
    capped = 0
    for r in rows:
        verdict = json.loads(r["verdict"])
        ocr = conn.execute("SELECT group_concat(ocr_text, char(10)) FROM media "
                           "WHERE post_id = ? AND ocr_text <> ''", (r["id"],)).fetchone()[0]
        text = "\n".join(x for x in (r["text"], ocr) if x)
        if not cap_unbacked_alert(verdict, text, cfg["car"], threshold):
            continue
        capped += 1
        print(f"  post {r['id']}: {verdict['model_score']} -> {verdict['score']}  "
              f"[{verdict.get('tier')}] {str(verdict.get('reasons'))[:90]}")
        if a.apply:
            db.set_verdict(r["id"], verdict)
    verb = "capped" if a.apply else "would cap (rerun with --apply)"
    print(f"\n{len(rows)} posts at score >= {threshold}; {verb} {capped}")


if __name__ == "__main__":
    main()
