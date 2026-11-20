"""Quality checks for the prose reader that need NO answer key.

READ-ONLY (mode=ro). The benchmark grades the reader against what a model
found; these checks ask different questions, and each one can fail on its own:

  1 grounding    every emitted identifier passes the same gate ingest uses
  2 shape        emitted "chassis" that look like phones, years or junk
  3 contacts     emitted identifiers that the registry knows as a phone number
  4 corroborated emitted identifiers the registry already holds from ANOTHER
                 post (an independent read agreeing that it is a vehicle)
  5 speed        slowest posts (a regex that backtracks would show here)
  6 sample       a stratified sample written to a file for a blind reader

Usage:
    python scripts/prose_audit.py [--scope backlog|scored] [--sample out.jsonl]
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from carhunt import prose  # noqa: E402
from carhunt.gates import grounded, source_runs  # noqa: E402
from carhunt.kashf import looks_like_kashf  # noqa: E402
from carhunt.normalize import norm_chassis, norm_plate  # noqa: E402

# a 9-digit chassis starting 9 or 1 is common and real: only the full shapes
PHONE = re.compile(r"^(?:0[19]\d{8}|(?:00)?249[19]\d{8})$")
YEAR = re.compile(r"^(?:19[5-9]\d|20[0-3]\d)$")


def line_of(text: str, ident: str) -> tuple[str, str, str]:
    """(previous line, the line holding the identifier, next line)."""
    lines = [l.strip() for l in prose.clean(text).splitlines() if l.strip()]
    for i, l in enumerate(lines):
        if ident in l:
            return (lines[i - 1] if i else "", l, lines[i + 1] if i + 1 < len(lines) else "")
    return ("", "", "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "carhunt.db"))
    ap.add_argument("--scope", choices=["backlog", "scored"], default="backlog")
    ap.add_argument("--sample", help="write a blind-review sample to this JSONL file")
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=60)
    cond = "IS NULL" if args.scope == "backlog" else "IS NOT NULL"
    rows = conn.execute(
        f"SELECT p.id, p.text FROM posts p WHERE p.score {cond} AND p.text IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id)").fetchall()

    # registry knowledge, by value -> the posts that hold it
    posts_of = defaultdict(set)
    for norm, pid in conn.execute(
            "SELECT chassis_norm, post_id FROM vehicles WHERE chassis_norm IS NOT NULL"):
        posts_of[norm].add(pid)
    for norm, pid in conn.execute(
            "SELECT plate_norm, post_id FROM vehicles WHERE plate_norm IS NOT NULL"):
        posts_of[norm].add(pid)
    contacts = set()
    for (contact,) in conn.execute(
            "SELECT DISTINCT contact FROM vehicles WHERE contact IS NOT NULL"):
        for run in re.findall(r"\d{9,}", str(contact).translate(prose.fold_digits.__globals__["_DIGITS"])):
            contacts.add(run.lstrip("0"))

    c = Counter()
    by_rule = defaultdict(Counter)
    slow = []
    distinct = {}                       # norm -> (pid, raw, rule)
    flagged = defaultdict(list)
    for pid, text in rows:
        if not text.strip() or looks_like_kashf(text):
            continue
        t0 = time.perf_counter()
        result = prose.extract(text)
        dt = time.perf_counter() - t0
        slow.append((dt, pid, len(text)))
        c["posts"] += 1
        if not result["settled"]:
            c["unsettled"] += 1
            continue
        c["settled"] += 1
        runs = source_runs(text)
        for v in result["vehicles"]:
            for field, norm_fn in (("chassis", norm_chassis), ("plate", norm_plate)):
                raw = v.get(field)
                if not raw:
                    continue
                rule = v["rule"] if field == "chassis" or v["rule"] == "list" else "plate"
                r = by_rule[rule]
                r["emitted"] += 1
                c["emitted"] += 1
                norm = norm_fn(raw)
                if not grounded(raw, runs):
                    r["UNGROUNDED"] += 1
                    flagged["ungrounded"].append((pid, raw, rule))
                digits = re.sub(r"\D", "", norm)
                if field == "chassis":
                    if PHONE.match(digits) and digits == norm:
                        r["phone-shaped"] += 1
                        flagged["phone-shaped"].append((pid, raw, rule))
                    if YEAR.match(norm):
                        r["year-shaped"] += 1
                        flagged["year-shaped"].append((pid, raw, rule))
                    if len(digits) < 4:
                        r["under 4 digits"] += 1
                    if len(norm) > 17:
                        r["over 17 chars"] += 1
                        flagged["over 17 chars"].append((pid, raw, rule))
                    if len(set(digits)) == 1 and len(digits) >= 4:
                        r["one repeated digit"] += 1
                        flagged["one repeated digit"].append((pid, raw, rule))
                if digits.lstrip("0") in contacts and len(digits) >= 10:
                    r["known as a contact"] += 1
                    flagged["known as a contact"].append((pid, raw, rule))
                others = posts_of.get(norm, set()) - {pid}
                if others:
                    r["corroborated"] += 1
                if norm not in distinct:
                    distinct[norm] = (pid, raw, rule, bool(others))

    print(f"scope: {args.scope}   posts {c['posts']}   settled {c['settled']}"
          f"   emitted identifiers {c['emitted']}   distinct {len(distinct)}")
    cols = ["emitted", "corroborated", "UNGROUNDED", "phone-shaped", "known as a contact",
            "year-shaped", "under 4 digits", "over 17 chars", "one repeated digit"]
    print("\nrule         " + "  ".join(f"{h:>12s}" for h in cols))
    for rule, r in sorted(by_rule.items(), key=lambda kv: -kv[1]["emitted"]):
        print(f"{rule:12s} " + "  ".join(f"{r[h]:12d}" for h in cols))
    corr = sum(1 for v in distinct.values() if v[3])
    print(f"\ndistinct identifiers also held by another post: {corr} of {len(distinct)}"
          f" ({100 * corr / max(len(distinct), 1):.1f}%)")

    slow.sort(reverse=True)
    total = sum(s[0] for s in slow)
    print(f"\nspeed: {total:.0f}s total, slowest posts:")
    for dt, pid, n in slow[:5]:
        print(f"  post {pid}: {dt * 1000:.0f} ms for {n} chars")

    random.seed(args.seed)
    for name, items in flagged.items():
        print(f"\n--- {name}: {len(items)} (showing up to 12) ---")
        texts = dict(rows) if len(rows) < 400000 else {}
        for pid, raw, rule in random.sample(items, min(12, len(items))):
            print(f"  [{pid}] {rule:6s} {raw:>20s} | {line_of(texts.get(pid, ''), raw)[1][:150]}")

    if args.sample:
        # stratified: uncorroborated values first (nothing else vouches for
        # them), and the prose rules over-sampled against the list rule
        texts = dict(rows)
        pool = list(distinct.items())
        strata = {
            "uncorroborated list": [x for x in pool if x[1][2] == "list" and not x[1][3]],
            "uncorroborated prose rules": [x for x in pool if x[1][2] != "list" and not x[1][3]],
            "corroborated": [x for x in pool if x[1][3]],
        }
        share = {"uncorroborated list": 0.4, "uncorroborated prose rules": 0.4, "corroborated": 0.2}
        out = []
        for name, items in strata.items():
            for norm, (pid, raw, rule, _) in random.sample(items, min(int(args.n * share[name]), len(items))):
                prev, line, nxt = line_of(texts[pid], raw)
                out.append({"post": pid, "number": raw, "previous_line": prev[:200],
                            "line": line[:300], "next_line": nxt[:200],
                            "_stratum": name, "_rule": rule})
        random.shuffle(out)
        with open(args.sample, "w", encoding="utf-8") as f:
            for i, row in enumerate(out, 1):
                f.write(json.dumps({"n": i, **row}, ensure_ascii=False) + "\n")
        print(f"\nblind-review sample: {len(out)} rows -> {args.sample}")


if __name__ == "__main__":
    main()
