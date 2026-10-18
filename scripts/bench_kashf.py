"""Measure the deterministic كشف parser against the trusted pipeline's output.

Same discipline every provider here has had to pass: it is scored on posts the
local Sonnet/vision pipeline ALREADY extracted and reconciled, so every answer
has a known-good counterpart. Nothing is written to the database.

The two numbers that decide it:

  RECALL       — of the chassis the trusted pipeline found, how many does the
                 parser also find? Below parity it is not a replacement.
  FABRICATION  — of the chassis the parser emits, how many are absent from the
                 post's own text? For a parser this MUST be exactly zero: it
                 copies digit runs, so anything else is a bug in the copying.

EXTRA is reported but is not automatically a fault. A parser that reads a
truncated line an LLM gave up on produces a real identifier with no
counterpart in the reference — the same way the OCR pass found 537 chassis
nobody had. Extras are therefore printed for reading, never scored blindly.

    python scripts\\bench_kashf.py            # every mega-list post with truth
    python scripts\\bench_kashf.py --limit 20 --show 3
"""
import argparse
import pathlib
import re
import sys
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from carhunt import db
from carhunt.kashf import fold_digits, looks_like_kashf, parse
from carhunt.normalize import norm_chassis

# Posts that are list-shaped AND already carry trusted vehicle rows. Driven
# from `vehicles` and joined back by primary key: vehicles.post_id has NO
# index, so an EXISTS(...) correlated subquery is a 360k-row scan per post and
# does not finish — the same trap documented in openai_backlog.py.
SQL = """
SELECT p.id, p.text, COUNT(*) AS n
FROM vehicles v JOIN posts p ON p.id = v.post_id
WHERE v.chassis_norm IS NOT NULL AND LENGTH(COALESCE(p.text,'')) > 2000
GROUP BY p.id
HAVING n >= 20
ORDER BY n DESC
"""


def digits_of(value: str) -> str:
    return re.sub(r"\D", "", unicodedata.normalize("NFKC", str(value or "")))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--show", type=int, default=2,
                    help="how many posts to print misses/extras for, by eye")
    args = ap.parse_args()

    conn = db.connect()
    rows = conn.execute(SQL).fetchall()
    if args.limit:
        rows = rows[:args.limit]

    tot_truth = tot_hit = tot_emit = tot_extra = tot_fab = 0
    tot_residue = tot_posts = 0
    shown = 0

    for r in rows:
        text = r["text"] or ""
        if not looks_like_kashf(text):
            continue
        tot_posts += 1

        truth = {v["chassis_norm"] for v in conn.execute(
            "SELECT chassis_norm FROM vehicles WHERE post_id=? "
            "AND chassis_norm IS NOT NULL", (r["id"],)).fetchall()}

        out = parse(text)
        got = {norm_chassis(v["chassis"]) for v in out["vehicles"] if v["chassis"]}
        got.discard("")

        hit = truth & got
        missed = truth - got
        extra = got - truth

        # Fabrication for a PARSER means: emitted digits that are not in the
        # post's own text. That should be structurally impossible; if it fires,
        # the copying is broken.
        src = digits_of(fold_digits(text))
        fab = [c for c in got if digits_of(c) and digits_of(c) not in src]

        tot_truth += len(truth); tot_hit += len(hit); tot_emit += len(got)
        tot_extra += len(extra); tot_fab += len(fab)
        tot_residue += len(out["residue"])

        if shown < args.show and (missed or extra):
            shown += 1
            print(f"\n--- post {r['id']}: truth={len(truth)} parsed={len(got)} "
                  f"hit={len(hit)} missed={len(missed)} extra={len(extra)} "
                  f"residue_lines={len(out['residue'])}")
            for c in list(missed)[:8]:
                print(f"    MISSED {c}")
            for c in list(extra)[:8]:
                print(f"    EXTRA  {c}")

    recall = tot_hit / tot_truth if tot_truth else 0
    print("\n" + "=" * 66)
    print(f"KASHF PARSER vs the trusted pipeline — {tot_posts} list posts")
    print("=" * 66)
    print(f"  chassis in reference       : {tot_truth}")
    print(f"  chassis parser found       : {tot_hit}   (recall {recall:.1%})")
    print(f"  chassis parser MISSED      : {tot_truth - tot_hit}")
    print(f"  chassis parser emitted     : {tot_emit}")
    print(f"  EXTRA (not in reference)   : {tot_extra}   "
          f"(read them — may be real finds the LLM truncated away)")
    print(f"  FABRICATED (not in text)   : {tot_fab}   <- must be 0")
    print(f"  residue lines for an LLM   : {tot_residue}")


if __name__ == "__main__":
    main()
