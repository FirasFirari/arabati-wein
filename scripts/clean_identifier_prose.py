"""Strip analyst commentary that leaked into chassis/plate fields.

Agents keep writing explanations INTO the identifier field, e.g.
    chassis = "5223949 (from text; does not match target VIN fragment <target tail>)"
`chassis_norm` folds that to ...FRAGMENT<target tail>, which then matches a
`%<target tail>%` search. That single row produced a phantom hit on the exact number
this project exists to find.

The dangerous direction is the opposite one: a REAL match arriving with prose
appended would have a chassis_norm that no exact-match query can find. A
missed match is the whole failure mode of the search.

Rule: a VIN is at most 17 characters. Anything longer is contaminated. Take
the leading identifier-looking token, preserve the full original text in the
description (never discard it), and recompute the norm.

Usage:
    python scripts/clean_identifier_prose.py [--apply]      # dry-run default
"""
import argparse
import pathlib
import re
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from carhunt.normalize import norm_chassis, norm_plate

# Single source of truth: the same guard runs at ingest time in
# ingest_local_verdicts.py. Two copies of this rule drifted apart once and a
# VIN prefix was destroyed - keep it in carhunt/normalize.py only.
from carhunt.normalize import has_prose, strip_prose  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default="data/carhunt.db")
    a = ap.parse_args()

    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, chassis, chassis_norm, plate, plate_norm, description
          FROM vehicles
         WHERE (chassis_norm IS NOT NULL AND length(chassis_norm) > 17)
            OR chassis LIKE '%(%' OR plate LIKE '%(%'
    """).fetchall()

    fixed = multi = 0
    for r in rows:
        upd, notes = {}, []
        for field, normfn in (("chassis", norm_chassis), ("plate", norm_plate)):
            val = r[field]
            if not val:
                continue
            if not has_prose(val):
                continue          # several bare numbers - leave every one alone
            h = strip_prose(val)
            if h == val or h is None:
                continue          # nothing to do, or nothing survived: keep original
            upd[field] = h
            # norm on the FIRST token: chassis_norm is a single-value column and
            # gluing two numbers together is what created the phantom in the
            # first place. Every token stays in the text field and description.
            upd[field + "_norm"] = normfn(h.split(" / ")[0])
            notes.append(f"original {field}: {val}")
            if len(h.split(" / ")) > 1:
                multi += 1
        if not upd:
            continue
        desc = (r["description"] or "")
        for n in notes:
            if n not in desc:
                desc = (desc + " | " if desc else "") + n
        upd["description"] = desc
        fixed += 1
        if fixed <= 6:
            for f in ("chassis", "plate"):
                if f in upd:
                    print(f"  id {r['id']} {f}:")
                    print(f"      before {r[f]!r}")
                    print(f"      after  {upd[f]!r}")
        if a.apply:
            sets = ", ".join(f"{k} = ?" for k in upd)
            conn.execute(f"UPDATE vehicles SET {sets} WHERE id = ?",
                         list(upd.values()) + [r["id"]])

    if a.apply:
        conn.commit()
    print(f"\ncandidate rows : {len(rows)}")
    print(f"rows {'updated' if a.apply else 'that WOULD change'} : {fixed}")
    print(f"rows holding more than one identifier (all kept): {multi}")
    if not a.apply:
        print("\ndry run - re-run with --apply to write")


if __name__ == "__main__":
    main()
