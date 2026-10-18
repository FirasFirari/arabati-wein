"""Recompute vehicles.plate_norm from the stored plate text.

Two reasons a stored value can be wrong:
  * it was written by an older norm_plate that flattened every digit, so the
    region marker was absorbed into the serial ("خ1/ 33056" -> "133056"), and
    a family searching the serial they remember would miss the row;
  * the region marker was glued to the serial with no separator ("خ440551"),
    which the longest-run rule could not split until now.

plate_norm is a derived search index, so recomputing is safe and idempotent.

Usage:
    python scripts/recompute_plate_norms.py [--dry-run]
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from carhunt import db
from carhunt.normalize import norm_plate


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    db.init()
    conn = db.connect()
    rows = conn.execute(
        "SELECT id, plate, plate_norm FROM vehicles WHERE plate IS NOT NULL").fetchall()

    changed = []
    for r in rows:
        new = norm_plate(r["plate"]) or None
        if new != r["plate_norm"]:
            changed.append((r["id"], r["plate"], r["plate_norm"], new))

    print(f"{len(rows)} vehicles carry a plate")
    print(f"{len(changed)} stored plate_norm values are wrong")
    for _id, plate, old, new in changed[:15]:
        print(f"   {plate!r:24} {old!r:12} -> {new!r}")
    if len(changed) > 15:
        print(f"   ... and {len(changed) - 15} more")

    if a.dry_run:
        print("\ndry run — nothing written")
        return

    with conn:
        for _id, _plate, _old, new in changed:
            conn.execute("UPDATE vehicles SET plate_norm = ? WHERE id = ?", (new, _id))
    print(f"\nupdated {len(changed)} rows")

    # a plate should normally normalise to 4-6 digits; anything longer usually
    # means a region digit is still stuck to the serial
    long = conn.execute(
        "SELECT COUNT(*) c FROM vehicles WHERE plate_norm IS NOT NULL "
        "AND LENGTH(plate_norm) > 6").fetchone()["c"]
    print(f"plate_norm values still longer than 6 digits: {long}")


if __name__ == "__main__":
    main()
