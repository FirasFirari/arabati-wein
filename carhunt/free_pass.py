"""Settle everything in the queue that needs no API call.

Runs the three free gates from the normal pipeline over every unscored post:
  1. deterministic identifier scan — the check that actually matters
  2. relevance filter (source-aware)
  3. duplicate-content verdict reuse

Whatever survives genuinely needs a model, and is left queued. Safe to run
any time, including while the API is unavailable.

Usage:
    python -m carhunt.free_pass
"""
import sys

from . import db
from .config import load_config
from .prefilter import identifier_hit, is_relevant


def main() -> None:
    cfg = load_config()
    car = cfg["car"]
    general = [s.lower() for s in (cfg.get("general_sources") or [])]
    db.init()
    conn = db.connect()

    rows = conn.execute(
        "SELECT p.id, p.source, p.text, "
        "(SELECT COUNT(*) FROM media m WHERE m.post_id = p.id) AS nmedia "
        "FROM posts p WHERE p.score IS NULL ORDER BY p.id").fetchall()
    print(f"{len(rows)} unscored posts\n")

    hits = prefiltered = duplicates = needs_model = 0
    for r in rows:
        media = ["x"] * r["nmedia"]

        hit = identifier_hit(r["text"], car)
        if hit:
            hits += 1
            db.save_analysis(r["id"], {
                "score": 95, "is_car_post": True, "model_match": True,
                "tier": "identifier_scan",
                "reasons": f"DETERMINISTIC IDENTIFIER MATCH: {hit} — found by "
                           f"exact text scan. VERIFY BEFORE ACTING."})
            url = conn.execute("SELECT url FROM posts WHERE id = ?",
                               (r["id"],)).fetchone()["url"]
            print(f"  !! post {r['id']}: {hit}\n     {url}")
            continue

        src = (r["source"] or "").lower()
        strict = any(g in src for g in general)
        if not is_relevant(r["text"], media, strict=strict):
            prefiltered += 1
            db.save_analysis(r["id"], {
                "score": 0, "is_car_post": False, "tier": "prefiltered",
                "reasons": "No vehicle vocabulary, chassis-like number or image."})
            continue

        if not media:
            chash = db.content_hash(r["text"])
            if chash:
                db.set_content_hash(r["id"], chash)
                prior = db.verdict_for_hash(chash, r["id"])
                if prior:
                    duplicates += 1
                    v = dict(prior)
                    v["tier"] = "duplicate"
                    v["vehicles_copied_from_duplicate"] = True
                    db.save_analysis(r["id"], v)
                    continue

        needs_model += 1

    print(f"\nidentifier hits (free):   {hits}")
    print(f"prefiltered out (free):   {prefiltered}")
    print(f"duplicates reused (free): {duplicates}")
    print(f"still needs a model call: {needs_model}")
    settled = hits + prefiltered + duplicates
    if rows:
        print(f"=> queue reduced by {settled} ({100 * settled / len(rows):.1f}%) "
              f"at zero cost")


if __name__ == "__main__":
    main()
