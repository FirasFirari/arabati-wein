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
from .normalize import digits_only
from .watches import match_reason, readings

TEXT_FIELDS = ("make", "model", "color", "year", "status", "location", "description")


def score_vehicle(query: str, vehicle: dict) -> tuple[int, list[str]]:
    """Return (score, reasons) for how well a vehicle matches the query.

    Identifier matching is the standing-watch rule (carhunt/watches.py), which
    the published page now mirrors and tests/test_search_parity.py checks:
    this CLI used to carry a third, older rule that flattened a plate's region
    digit into its serial and had no plate-shaped guard.
    """
    score, reasons = 0, []
    if len(digits_only(query)) >= 4:
        qn, qp = readings(query)
        why = match_reason(qn, qp, vehicle.get("chassis_norm") or "", vehicle.get("plate_norm"))
        if why:
            score += 60 if "plate" in why else 40 + 8 * min(len(qn), 17)
            reasons.append(why)

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
