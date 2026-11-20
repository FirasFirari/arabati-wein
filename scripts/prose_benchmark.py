"""Measure a deterministic extractor against posts a model has already read.

READ-ONLY. Opens the database with mode=ro and writes nothing to it.

The answer key is every text-only post (no photos) that a language model
scored, with the identifiers that model extracted AND that pass the grounding
gate (an ungrounded value would have been nulled on ingest, so it is not part
of the key). The extractor under test is `carhunt.prose.extract`.

Two numbers decide whether the extractor may ever write a row:

  recall   of the key's identifiers, how many the extractor also found
  extras   identifiers the extractor emitted that the key does not hold.
           An extra is EITHER a model miss (good) OR a number that is not a
           vehicle identifier at all (a price, a mileage, a phone: bad). Only
           reading them tells which, so `--show extras` prints a sample with
           its source line.

Usage:
    python scripts/prose_benchmark.py                 # summary
    python scripts/prose_benchmark.py --show misses   # key ids the extractor lost
    python scripts/prose_benchmark.py --show extras   # ids only the extractor has
    python scripts/prose_benchmark.py --tiers sonnet  # only the strongest key
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from carhunt import prose  # noqa: E402
from carhunt.gates import grounded, source_runs  # noqa: E402
from carhunt.kashf import looks_like_kashf  # noqa: E402
from carhunt.normalize import has_prose, norm_chassis, norm_plate  # noqa: E402

MODEL_TIERS = {
    "sonnet": {"local_claude_sonnet", "local_claude_ocr", "local_reextract"},
    "deep": {"gemini_deep", "vertex_deep", "openai_deep", "deep"},
    "screen": {"gemini_screen", "vertex_screen", "openai_screen",
               "batch_screen", "screen"},
}


def tier_group(tier: str) -> str | None:
    for group, names in MODEL_TIERS.items():
        if tier in names:
            return group
    return None


def key_ids(vehicles, runs) -> set[str]:
    """Normalised identifiers of a verdict's vehicles that the gate would keep."""
    out = set()
    for v in vehicles or []:
        if not isinstance(v, dict):
            continue
        for field, norm in (("chassis", norm_chassis), ("plate", norm_plate)):
            raw = v.get(field)
            if not raw or has_prose(str(raw)):
                continue
            for piece in re.split(r"\s*/\s*|\s*,\s*", str(raw)):
                if not piece.strip() or not grounded(piece, runs):
                    continue
                n = norm(piece)
                if len(re.sub(r"\D", "", n)) >= 4:
                    out.add(n)
    return out


def found_ids(result) -> dict[str, str]:
    """normalised identifier -> the rule that produced it"""
    out = {}
    for v in result["vehicles"]:
        if v.get("chassis"):
            out.setdefault(norm_chassis(v["chassis"]), v["rule"])
        if v.get("plate"):
            n = norm_plate(v["plate"])
            if len(n) >= 3:
                out.setdefault(n, "plate" if v["rule"] != "list" else "list")
    out.pop("", None)
    return out


def match(a: str, pool: set[str]) -> str | None:
    """'exact', 'partial' (one contains the other, 4+ chars) or None."""
    if a in pool:
        return "exact"
    for b in pool:
        short, long_ = (a, b) if len(a) <= len(b) else (b, a)
        if len(short) >= 4 and short in long_:
            return "partial"
    return None


def context(text: str, ident: str) -> str:
    """The source line an identifier sits on, for reading by eye."""
    folded = prose.fold_digits(text)
    digits = re.sub(r"\D", "", ident)
    for line in folded.splitlines():
        if ident in line.upper() or (digits and digits in re.sub(r"[\s\-]", "", line)):
            return line.strip()[:200]
    return "(line not found)"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "carhunt.db"))
    ap.add_argument("--tiers", default="sonnet,deep,screen")
    ap.add_argument("--show", choices=["misses", "extras", "unsettled", "fields"])
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--rule", help="with --show extras: only this rule")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--holdout", action="store_true",
                    help="grade on the TEXT of photo posts instead: posts the rules "
                         "were never tuned on. The key is what the model read that "
                         "is in the post text (not only in a photo).")
    args = ap.parse_args()
    groups = set(args.tiers.split(","))

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=60)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT p.id, p.text, p.verdict FROM posts p WHERE p.score IS NOT NULL "
        f"AND p.text IS NOT NULL AND {'EXISTS' if args.holdout else 'NOT EXISTS'} "
        "(SELECT 1 FROM media m WHERE m.post_id = p.id) ORDER BY p.id").fetchall()

    c = Counter()
    by_group = {g: Counter() for g in groups}
    misses, extras, unsettled, fields = [], [], [], []
    rules = defaultdict(Counter)
    for r in rows:
        try:
            verdict = json.loads(r["verdict"] or "{}")
        except ValueError:
            continue
        group = tier_group(verdict.get("tier") or "")
        if group not in groups:
            continue
        text = r["text"]
        if looks_like_kashf(text):
            continue                      # the list parser's job, not this one
        if args.limit and c["posts"] >= args.limit:
            break
        c["posts"] += 1
        g = by_group[group]
        g["posts"] += 1

        key = key_ids(verdict.get("vehicles"), source_runs(text))
        result = prose.extract(text)
        got = found_ids(result) if result["settled"] else {}

        if result["settled"]:
            c["settled"] += 1
            c["settled_" + result["mode"]] += 1
            g["settled"] += 1
        else:
            c["unsettled"] += 1
            unsettled.append((r["id"], result["why"], text))
            c["why: " + result["why"]] += 1
            continue                      # a model reads it; nothing to grade

        if key:
            c["settled_key_posts"] += 1
        for k in key:
            c["key_ids"] += 1
            g["key_ids"] += 1
            m = match(k, set(got))
            if m:
                c["found_" + m] += 1
                g["found"] += 1
            else:
                c["missed"] += 1
                misses.append((r["id"], k, context(text, k)))
        post_extra = False
        for x, rule in got.items():
            c["got_ids"] += 1
            rules[rule]["emitted"] += 1
            if len(x) < 4:
                rules[rule]["short (ungraded)"] += 1
                continue                  # the key holds nothing this short
            if not match(x, key):
                rules[rule]["extra"] += 1
                c["extra"] += 1
                g["extra"] += 1
                post_extra = True
                extras.append((r["id"], rule + " " + x, context(text, x)))
        if post_extra:
            c["posts_with_extra"] += 1

        ref = [v for v in verdict.get("vehicles") or [] if isinstance(v, dict)]
        if len(ref) == 1 and len(result["vehicles"]) == 1:
            mine = result["vehicles"][0]
            for f in ("year", "status"):
                a, b = str(ref[0].get(f) or ""), str(mine.get(f) or "")
                c[f"{f}_both"] += 1
                if a == b:
                    c[f"{f}_same"] += 1
                else:
                    fields.append((r["id"], f, a, b, ""))
        c["veh_ref_any" if ref else "veh_ref_none"] += 1
        if bool(ref) == bool(result["vehicles"]):
            c["veh_presence_same"] += 1

    def pct(a, b):
        return f"{100 * a / b:.1f}%" if b else "n/a"

    print(f"posts read by a model, text only, not lists: {c['posts']}")
    print(f"  settled by the extractor : {c['settled']} ({pct(c['settled'], c['posts'])})")
    print(f"  left for a model         : {c['unsettled']} ({pct(c['unsettled'], c['posts'])})")
    for k, v in sorted(c.items()):
        if k.startswith("why: "):
            print(f"      {k[5:]}: {v}")
    print("\nON THE SETTLED POSTS")
    found = c["found_exact"] + c["found_partial"]
    print(f"  identifiers in the key   : {c['key_ids']} (in {c['settled_key_posts']} posts)")
    print(f"  found                    : {found} ({pct(found, c['key_ids'])})"
          f"  [exact {c['found_exact']}, partial {c['found_partial']}]")
    print(f"  missed                   : {c['missed']}")
    print(f"  extractor identifiers    : {c['got_ids']}")
    print(f"  extras (not in the key)  : {c['extra']} in {c['posts_with_extra']} posts"
          f" ({pct(c['extra'], c['got_ids'])} of what it emitted)")
    print(f"  vehicle present/absent agrees with the model: "
          f"{pct(c['veh_presence_same'], c['settled'])}")
    for f in ("year", "status"):
        print(f"  {f} agrees (one-vehicle posts): "
              f"{pct(c[f + '_same'], c[f + '_both'])} of {c[f + '_both']}")
    print(f"  settled as list / prose  : {c['settled_list']} / {c['settled_prose']}")
    print("\nBY RULE (extras = model misses + wrong numbers; read them)")
    for rule, rc in sorted(rules.items(), key=lambda kv: -kv[1]["emitted"]):
        print(f"  {rule:12s} emitted {rc['emitted']:6d}  extras {rc['extra']:5d}")
    print("\nBY ANSWER-KEY STRENGTH")
    for name in ("sonnet", "deep", "screen"):
        g = by_group.get(name)
        if g:
            print(f"  {name:7s} posts {g['posts']:6d}  settled {pct(g['settled'], g['posts']):>6s}"
                  f"  recall {pct(g['found'], g['key_ids']):>6s} of {g['key_ids']:5d}"
                  f"  extras {g['extra']}")

    random.seed(args.seed)
    pools = {"misses": misses, "extras": extras, "unsettled": unsettled,
             "fields": fields}
    if args.show:
        pool = pools[args.show]
        if args.rule:
            pool = [x for x in pool if str(x[1]).startswith(args.rule + " ")]
        print(f"\n--- {args.show}: {min(args.n, len(pool))} of {len(pool)} ---")
        for item in random.sample(pool, min(args.n, len(pool))):
            if args.show == "unsettled":
                print(f"\n[{item[0]}] {item[1]}\n{item[2][:400]}")
            elif args.show == "fields":
                print(f"[{item[0]}] {item[1]}: model={item[2]!r} extractor={item[3]!r}")
            else:
                print(f"[{item[0]}] {item[1]:>28s} | {item[2]}")


if __name__ == "__main__":
    main()
