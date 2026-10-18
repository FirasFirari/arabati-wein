"""Batch already-collected posts whose vehicle lists were never fully read.

A post qualifies when its text carries many chassis-like numbers but few
vehicle rows came out of it. Three causes, all found in the 2026-08-24 audit:
  * a screening tier handled it and screening never extracts vehicles;
  * the offline heuristic handled it, with no model involved at all;
  * the deep tier read it but ran out of output tokens — a 25k-character list
    needs ~56k output tokens to write back and the cap is 16k.

Posts already handled by the local pipeline, and duplicate-tier posts (whose
vehicles live on the original), are skipped.

Usage:
    python scripts/make_reextract_batches.py [--outdir DIR] [--min-numbers 20]
"""
import argparse
import json
import pathlib
import re
import sys
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from carhunt import db

AR = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
LONG_NUM = re.compile(r"\b\d{5,}\b")
SKIP_TIERS = {"duplicate", "local_claude_sonnet", "identifier_scan", "owner_test_record",
              "owner_posting", "codex_manual_visual"}


def numbers_in(text: str) -> int:
    return len(LONG_NUM.findall(unicodedata.normalize("NFKC", text or "").translate(AR)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", type=pathlib.Path, default=pathlib.Path("data/reextract_batches"))
    ap.add_argument("--min-numbers", type=int, default=20)
    ap.add_argument("--ratio", type=float, default=0.4)
    ap.add_argument("--nums", type=int, default=250, help="chassis-number budget per batch")
    a = ap.parse_args()

    db.init()
    conn = db.connect()
    rows = conn.execute("""
        SELECT p.id, p.source, p.url, p.text, p.verdict, p.score,
               (SELECT COUNT(*) FROM vehicles WHERE post_id = p.id) AS veh
        FROM posts p
        WHERE p.score IS NOT NULL AND LENGTH(p.text) > 1500
        ORDER BY p.id""").fetchall()

    todo = []
    for r in rows:
        try:
            tier = (json.loads(r["verdict"] or "{}") or {}).get("tier")
        except Exception:
            tier = None
        if tier in SKIP_TIERS:
            continue
        n = numbers_in(r["text"])
        if n >= a.min_numbers and r["veh"] < n * a.ratio:
            todo.append({"id": r["id"], "source": r["source"], "url": r["url"],
                         "text": r["text"], "_n": n, "_had": r["veh"], "_tier": tier})

    a.outdir.mkdir(parents=True, exist_ok=True)
    for old in a.outdir.glob("batch_*.json"):
        old.unlink()

    batches, cur, budget = [], [], 0
    for p in todo:
        if cur and budget + p["_n"] > a.nums:
            batches.append(cur); cur, budget = [], 0
        cur.append(p); budget += p["_n"]
    if cur:
        batches.append(cur)

    for i, b in enumerate(batches, 1):
        clean = [{k: v for k, v in p.items() if not k.startswith("_")} for p in b]
        (a.outdir / f"batch_{i:03d}.json").write_text(
            json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")

    missing = sum(p["_n"] - p["_had"] for p in todo)
    print(f"posts needing re-extraction : {len(todo)}")
    print(f"chassis-like numbers unread : {missing:,}")
    print(f"estimated recoverable rows  : {int(missing * 0.89):,}  (0.89 ratio measured on local-pipeline posts)")
    print(f"batches written             : {len(batches)} -> {a.outdir}")
    from collections import Counter
    print("by tier :", Counter(p["_tier"] for p in todo).most_common())
    print("by source:", Counter(p["source"] for p in todo).most_common(6))


if __name__ == "__main__":
    main()
