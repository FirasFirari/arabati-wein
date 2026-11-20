"""Turn ordinary text posts into verdict files, with no model call.

This does NOT write to the database. Like kashf_verdicts.py it emits a
verdicts JSON and its batch JSON, so the posts go in through
`ingest_local_verdicts.py` and inherit every gate that path enforces.

    python scripts\\prose_verdicts.py --out data\\prose_w1 --limit 500   # build
    python scripts\\ingest_local_verdicts.py data\\prose_w1\\verdicts_prose.json ^
        --batch data\\prose_w1\\batch_prose.json --tier prose_parser

WHICH POSTS. Unscored, text only (no photo rows), not a كشف list (that is
kashf_verdicts.py's job), and BOTH of:

  * the reader settled it: every number in the post is explained as an
    identifier or as something that is plainly not one (carhunt/prose.py);
  * it found at least one chassis or plate.

The second condition is a decision, not a limitation (owner, 2026-11-20). A
post with no identifier is one where a model's reading is worth more than
this reader's: measured on 16,387 settled posts, a model wrote a vehicle row
on 1,609 where the reader wrote none (for-sale ads, "found a pickup in North
Kordofan"), and the reader never fills a location. Those posts stay queued
for the Gemini lane.

SCORING. As for the list parser: the reader has no opinion about whether a
post is the target car. `score` is the neutral 10 and the deterministic
identifier re-check in the alert path decides.

Measured before this was allowed to write anything (2026-11-19/20): 99.9%
recall against 364,414 model-read identifiers, 99.9% on a holdout of 26,456,
and a blind review of 300 emitted numbers found no phone, price or year among
them. Re-measure with scripts/prose_benchmark.py and scripts/prose_audit.py
after any change to the rules.
"""
import argparse
import copy
import json
import pathlib
import random
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from carhunt import db, prose
from carhunt.gates import check_entries
from carhunt.aliases import make_model_for
from carhunt.kashf import looks_like_kashf

SQL_UNSCORED = """
SELECT p.id, p.text, p.url FROM posts p
WHERE p.score IS NULL AND p.text IS NOT NULL AND p.text <> ''
  AND NOT EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id)
ORDER BY p.id DESC
"""


def _make_model(key):
    """make/model for a key. NOT a split on the first space: that put body types
    in the make field (41,101 rows said make "Pickup", model "(بوكس)")."""
    return make_model_for(key)


def build_verdict(text: str) -> dict | None:
    """The verdict for one post, or None if the post is not this lane's."""
    if looks_like_kashf(text):
        return None
    result = prose.extract(text)
    if not result["settled"]:
        return None
    vehicles, rules = [], Counter()
    for v in result["vehicles"]:
        if not (v.get("chassis") or v.get("plate")):
            continue
        make, model = _make_model(v.get("model_key"))
        rules[v["rule"]] += 1
        vehicles.append({
            "make": make, "model": model, "color": v.get("color"),
            "year": str(v["year"]) if v.get("year") else None,
            "chassis": v.get("chassis"), "plate": v.get("plate"),
            "status": v.get("status") or "unknown",
            "location": None, "contact": v.get("contact"),
            "description": v.get("description"),
            **({"low_confidence": v["low_confidence"]} if v.get("low_confidence") else {}),
        })
    if not vehicles:
        return None                 # no identifier: a model reads this post
    how = ", ".join(f"{n} by rule '{r}'" for r, n in rules.most_common())
    return {
        "is_car_post": True,
        "model_match": False,
        "score": 10,
        "reasons": (f"Text post read deterministically ({result['mode']} mode): "
                    f"{len(vehicles)} vehicle(s), {how}. Every number in the post "
                    f"was explained; no model judgement applied."),
        "vehicles": vehicles,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--random", action="store_true",
                    help="with --limit: a random sample instead of the newest posts")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--spot", type=int, default=40,
                    help="posts written out in full to spot_check.txt")
    args = ap.parse_args()

    db.init()
    conn = db.connect()
    rows = conn.execute(SQL_UNSCORED).fetchall()
    if args.random:
        random.Random(args.seed).shuffle(rows)

    verdicts, batch, readable = [], [], []
    c = Counter()
    for r in rows:
        c["text-only unscored posts looked at"] += 1
        verdict = build_verdict(r["text"])
        if verdict is None:
            c["left for another lane"] += 1
            continue
        # the gate the ingest will apply, run here on a copy: a rule that
        # emits something the gate would null is a bug to see BEFORE ingest
        changes = check_entries(copy.deepcopy(verdict["vehicles"]), r["text"],
                                photos_unread=False)
        if changes:
            c["posts the gate would change"] += 1
            c["gate changes"] += len(changes)
            if c["posts the gate would change"] <= 10:
                print(f"  gate: post {r['id']}: {changes[:3]}")
        c["vehicles"] += len(verdict["vehicles"])
        c["low_confidence rows"] += sum(1 for v in verdict["vehicles"] if v.get("low_confidence"))
        verdicts.append({"id": r["id"], "verdict": verdict})
        batch.append({"id": r["id"]})
        readable.append((r["id"], r["url"], r["text"], verdict))
        if args.limit and len(verdicts) >= args.limit:
            break

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "verdicts_prose.json").write_text(
        json.dumps(verdicts, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "batch_prose.json").write_text(
        json.dumps(batch, ensure_ascii=False, indent=1), encoding="utf-8")

    picks = random.Random(args.seed).sample(readable, min(args.spot, len(readable)))
    lines = ["SPOT CHECK: for each post, is every number under READ AS a real",
             "chassis or plate of a vehicle in the post, and is any vehicle number",
             "in the post missing from it?", ""]
    for n, (pid, url, text, verdict) in enumerate(picks, 1):
        lines += ["=" * 78, f"{n}. post {pid}   {url or '(no link)'}", "-" * 78,
                  text.strip()[:1500] + (" ..." if len(text) > 1500 else ""), "",
                  f"READ AS ({len(verdict['vehicles'])} vehicle(s)):"]
        for v in verdict["vehicles"][:60]:
            what = " ".join(x for x in (v["make"], v["model"], v["color"], v["year"]) if x)
            lines.append(f"   chassis {v['chassis'] or '-':<20} plate {v['plate'] or '-':<14}"
                         f" {what or '(no model)'} [{v['status']}]"
                         + (f"  LOW CONFIDENCE: {v['low_confidence']}" if v.get("low_confidence") else ""))
        if len(verdict["vehicles"]) > 60:
            lines.append(f"   ... and {len(verdict['vehicles']) - 60} more")
        lines.append("")
    (args.out / "spot_check.txt").write_text("\n".join(lines), encoding="utf-8")

    for k, v in c.items():
        print(f"{k:38s}: {v}")
    print(f"{'posts in this verdict file':38s}: {len(verdicts)}")
    print(f"\nwrote {args.out / 'verdicts_prose.json'}")
    print(f"      {args.out / 'batch_prose.json'}")
    print(f"      {args.out / 'spot_check.txt'}  ({len(picks)} posts in full)")
    print("\nIngest with the NORMAL path so every gate applies:")
    print(f"  python scripts\\ingest_local_verdicts.py "
          f"{args.out / 'verdicts_prose.json'} "
          f"--batch {args.out / 'batch_prose.json'} --tier prose_parser")


if __name__ == "__main__":
    main()
