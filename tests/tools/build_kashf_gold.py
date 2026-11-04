"""Freeze a كشف gold set that the parser did NOT write.

    python tests/tools/build_kashf_gold.py      # -> data/gold/kashf_gold.json

scripts/bench_kashf.py measured the parser against the `vehicles` table, but
after the parser's own waves 2,131 of its 2,277 "truth" posts were rows the
parser itself had written -- it was grading its own homework. This keeps only
list posts whose rows came from OTHER lanes (subagent waves, re-extraction,
Vertex), freezes text + truth to a file, and never reads the table again, so a
later ingest cannot move the answer key.

The output holds post text (contact numbers included), so it lives under
data/ (private, gitignored) and is read only by the private test
tests/test_kashf_gold.py. Read-only against the database.
"""
import collections
import json
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from carhunt import kashf  # noqa: E402

OUT = ROOT / "data" / "gold" / "kashf_gold.json"
PARSER_TIERS = {"kashf_parser"}


def main() -> None:
    con = sqlite3.connect(f"file:{ROOT / 'data' / 'carhunt.db'}?mode=ro", uri=True, timeout=60)
    tiers = {pid: t for pid, t in con.execute(
        "SELECT id, json_extract(verdict, '$.tier') FROM posts WHERE verdict IS NOT NULL")}
    candidates = {}
    for pid, text in con.execute("SELECT id, text FROM posts WHERE length(text) > 400"):
        if tiers.get(pid) in PARSER_TIERS or pid not in tiers:
            continue
        if text and kashf.looks_like_kashf(text):
            candidates[pid] = text
    truth = collections.defaultdict(set)
    # one pass over vehicles: there is no index on post_id
    for pid, cn in con.execute("SELECT post_id, chassis_norm FROM vehicles WHERE chassis_norm IS NOT NULL"):
        if pid in candidates and len(cn) >= 4:
            truth[pid].add(cn)
    posts = [{"post_id": pid, "tier": tiers[pid], "text": candidates[pid],
              "truth": sorted(truth[pid])}
             for pid in sorted(candidates) if len(truth[pid]) >= 5]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"built_from": "vehicles rows of non-parser tiers",
                               "posts": posts}, ensure_ascii=False), encoding="utf-8")
    n = sum(len(p["truth"]) for p in posts)
    by_tier = collections.Counter(p["tier"] for p in posts)
    print(f"{len(posts)} list posts, {n} truth chassis -> {OUT}")
    print("by tier:", dict(by_tier))


if __name__ == "__main__":
    main()
