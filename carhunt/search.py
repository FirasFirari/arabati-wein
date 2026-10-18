"""Search the vehicle database with any identifier: chassis (full VIN or
partial fragment, either digit script), plate number, or free text
(make/model/color/location, Arabic or English).

Usage:
    python -m carhunt.search 113335
    python -m carhunt.search "كورولا ابيض"
    python -m carhunt.search RKLBB9HE5F1122334   # a full VIN
"""
import argparse

from . import db
from .aliases import model_keys_in, normalize_ar
from .normalize import chassis_overlap, digits_only, plate_variants

TEXT_FIELDS = ("make", "model", "color", "year", "status", "location", "description")


def score_vehicle(query: str, vehicle: dict) -> tuple[int, list[str]]:
    """Return (score, reasons) for how well a vehicle matches the query."""
    score, reasons = 0, []
    q_digits = digits_only(query)

    if len(q_digits) >= 4:
        overlap = chassis_overlap(query, vehicle.get("chassis") or "")
        if overlap:
            score += 40 + overlap * 8  # longer overlap → much stronger
            reasons.append(f"chassis overlap ({overlap} chars): {vehicle['chassis']}")
        # match against each digit run of the stored plate: "12345 خ1" must be
        # found by someone typing just the serial they remember
        for run in plate_variants(vehicle.get("plate") or ""):
            if q_digits == run:
                score += 60
                reasons.append(f"plate match: {vehicle['plate']}")
                break

    terms = [t for t in query.replace("،", " ").split() if not digits_only(t) and len(t) >= 2]
    if terms:
        haystack = normalize_ar(" ".join(str(vehicle.get(f) or "") for f in TEXT_FIELDS))
        hit_terms = [t for t in terms if normalize_ar(t) in haystack]
        if hit_terms:
            score += 10 * len(hit_terms)
            reasons.append("text match: " + ", ".join(hit_terms))

    # alias/nickname/skeleton model matching: كرلا finds every Corolla
    query_keys = model_keys_in(query)
    if query_keys and vehicle.get("model_key") in query_keys:
        score += 25
        reasons.append(f"model match: {vehicle['model_key']}")

    return score, reasons


def search(query: str, limit: int = 15) -> list[dict]:
    results = []
    for vehicle in db.all_vehicles():
        score, reasons = score_vehicle(query, vehicle)
        if score > 0:
            vehicle["match_score"] = score
            vehicle["match_reasons"] = reasons
            results.append(vehicle)
    results.sort(key=lambda v: -v["match_score"])
    return results[:limit]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="+")
    parser.add_argument("--limit", type=int, default=15)
    args = parser.parse_args()
    query = " ".join(args.query)

    db.init()
    results = search(query, args.limit)
    if not results:
        print(f"No matches for: {query}")
        return
    for v in results:
        print(f"\n{'=' * 60}")
        print(f"Match score {v['match_score']} — {', '.join(v['match_reasons'])}")
        desc = " ".join(str(v[f]) for f in ("color", "make", "model", "year") if v.get(f))
        print(f"Vehicle: {desc or v.get('description', '?')}")
        for field in ("chassis", "plate", "status", "location", "contact", "description"):
            if v.get(field):
                print(f"  {field}: {v[field]}")
        print(f"Source: {v['source']} ({v['seen_at'][:10]})")
        if v.get("url"):
            print(f"Post: {v['url']}")


if __name__ == "__main__":
    main()
