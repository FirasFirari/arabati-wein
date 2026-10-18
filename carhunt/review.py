"""Review the strongest candidates collected so far.

Usage:
    python -m carhunt.review              # top 20 with score >= 40
    python -m carhunt.review --min 70     # only strong matches
"""
import argparse

from . import db


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min", type=int, default=40)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    db.init()
    candidates = db.top_candidates(limit=args.limit, min_score=args.min)
    if not candidates:
        print(f"No candidates with score >= {args.min} yet.")
        return

    for c in candidates:
        verdict = c["verdict"] or {}
        print(f"\n{'=' * 60}")
        print(f"Score {c['score']}/100 | {c['source']} | seen {c['seen_at'][:16]}")
        if c["url"]:
            print(f"Link: {c['url']}")
        print(f"Reasons: {verdict.get('reasons', '?')}")
        extracted = verdict.get("extracted") or {}
        for key, value in extracted.items():
            if value:
                print(f"  {key}: {value}")
        if c["text"]:
            print(f"Text: {c['text'][:300]}")
        for path in c["media"]:
            print(f"  photo: {path}")


if __name__ == "__main__":
    main()
