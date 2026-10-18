"""Turn كشف/حصر list posts into verdict files the normal ingest already accepts.

This does NOT write to the database. It emits the same two files a subagent
wave produces — a verdicts JSON and its batch JSON — so the mega-lists go in
through `ingest_local_verdicts.py` and inherit every gate that path already
enforces: coverage, the prose guard, the grounding gate (post text + OCR),
`--no-shrink`, and the score>=70 verification print. Building a second write
path would mean re-earning all of that, and this project has already paid for
those gates once each.

    python scripts\\kashf_verdicts.py --out data\\kashf_w1          # build
    python scripts\\verify_wave.py ...                              # optional
    python scripts\\ingest_local_verdicts.py data\\kashf_w1\\verdicts_kashf.json ^
        --batch data\\kashf_w1\\batch_kashf.json --tier kashf_parser

WHY THESE POSTS NEED THEIR OWN PATH. Every provider run defers them
(`--skip-over-chars 8000`) because reasoning shares the output budget and a
953-vehicle list either truncates or returns nothing. They are 1.5% of the
backlog and ~44% of its chassis numbers, so "deferred" has meant the densest
identifier source in the corpus was simply never ingested.

SCORING. The parser has no opinion about whether a post is the target car —
it reads numbers. `score` is therefore left at the neutral 10 that a recovery
list gets, and the DETERMINISTIC identifier re-check in the alert path is what
decides whether anything here matters. That check runs on `chassis_norm` after
ingest and does not depend on any model's judgement. Deliberately NOT scoring
these high: 900 recovery-list posts each claiming to be a possible match would
bury a real hit under noise.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from carhunt import db
from carhunt.aliases import ALIASES
from carhunt.kashf import looks_like_kashf, parse

# canonical model key -> ("Make", "Model") for the vehicle row. The key is
# already "toyota corolla" shaped, so this is a split, not a second table —
# aliases.py stays the single source of model knowledge.
def _make_model(key: str | None) -> tuple[str | None, str | None]:
    if not key:
        return None, None
    make, _, model = key.partition(" ")
    return make.title() or None, (model.title() or None)


SQL_UNSCORED = """
SELECT id, text FROM posts
WHERE score IS NULL AND LENGTH(COALESCE(text, '')) >= ?
ORDER BY LENGTH(text) DESC
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path, required=True,
                    help="directory for verdicts_kashf.json + batch_kashf.json")
    ap.add_argument("--min-chars", type=int, default=2000,
                    help="only consider posts at least this long; the list "
                         "detector still has the final say")
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--min-vehicles", type=int, default=5,
                    help="a 'list' that yields fewer than this is probably "
                         "prose with numbers in it — leave it to a model "
                         "rather than filing a thin, confident-looking row")
    args = ap.parse_args()

    db.init()
    conn = db.connect()
    rows = conn.execute(SQL_UNSCORED, (args.min_chars,)).fetchall()

    verdicts, batch = [], []
    tot_veh = tot_res = skipped_short = 0
    for r in rows:
        text = r["text"] or ""
        if not looks_like_kashf(text):
            continue
        out = parse(text)
        if len(out["vehicles"]) < args.min_vehicles:
            skipped_short += 1
            continue

        vehicles = []
        for v in out["vehicles"]:
            make, model = _make_model(v["model_key"])
            vehicles.append({
                "make": make, "model": model,
                "color": v["color"], "year": v["year"],
                "chassis": v["chassis"], "plate": v["plate"],
                # Recovery/inventory lists are sightings of FOUND vehicles.
                "status": "found",
                "description": v["description"],
            })
        tot_veh += len(vehicles)
        tot_res += len(out["residue"])

        verdicts.append({
            "id": r["id"],
            "verdict": {
                "is_car_post": True,
                # neutral: see the module docstring — the deterministic
                # identifier re-check decides, not this number
                "score": 10,
                "reasons": (
                    f"كشف/حصر inventory list parsed deterministically: "
                    f"{len(vehicles)} vehicles from {out['lines']} lines, "
                    f"{len(out['residue'])} line(s) left as residue for a "
                    f"model. No model judgement applied."
                ),
                "vehicles": vehicles,
            },
        })
        batch.append({"id": r["id"]})
        if args.limit and len(verdicts) >= args.limit:
            break

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "verdicts_kashf.json").write_text(
        json.dumps(verdicts, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "batch_kashf.json").write_text(
        json.dumps(batch, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"list posts parsed      : {len(verdicts)}")
    print(f"vehicles emitted       : {tot_veh}")
    print(f"residue lines for a model: {tot_res}")
    print(f"skipped (<{args.min_vehicles} vehicles, not really a list): {skipped_short}")
    print(f"\nwrote {args.out / 'verdicts_kashf.json'}")
    print(f"      {args.out / 'batch_kashf.json'}")
    print("\nIngest with the NORMAL path so every gate applies:")
    print(f"  python scripts\\ingest_local_verdicts.py "
          f"{args.out / 'verdicts_kashf.json'} "
          f"--batch {args.out / 'batch_kashf.json'} --tier kashf_parser")


if __name__ == "__main__":
    main()
