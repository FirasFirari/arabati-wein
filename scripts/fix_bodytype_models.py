"""Repair sedan makes wrongly assigned to a standalone body-type word.

`مضلع` is a BODY TYPE in Sudanese usage — the project alias table maps it to a
Toyota Hilux double-cab. When a post names a model alongside it ("اكسنت مضلع")
the named model wins and Hyundai Accent is correct; when the word stands alone
("مضلع 525193") there is no model, and the rule is explicit: use the body type
in English and leave the specific model null, never invent one.

One re-extraction agent inverted this and mapped standalone مضلع to Hyundai
Accent across hundreds of rows. This finds those and repairs them. It is
conservative: any row whose description carries a named-model token in ANY
observed spelling (Arabic or Persian kaf, with or without hamza) is left alone.

Usage:
    python scripts/fix_bodytype_models.py [--dry-run]
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from carhunt import db
from carhunt.aliases import normalize_ar

BODY = "مضلع"
BODY_EN = "double-cab pickup (مضلع)"
# every spelling of a named model seen alongside مضلع in this corpus
NAMED = ("اكسن", "أكسن", "اکسن", "اكسند", "اکسند", "افانت", "أفانت", "دبدوب",
         "فيرنا", "سحلي", "منفوخ", "كورول", "كوريل", "كورل", "سوناتا",
         "النترا", "لانسر", "كامري", "توسان", "سنتافي", "جيتز", "كلك")
SEDAN_MAKES = ("Hyundai", "Kia", "Nissan", "Mitsubishi")
# a MAKE stated alongside the body type is real information and is kept —
# "شريحه نيسان" is a Nissan van, so "كيا مضلع" is a Kia double-cab, not a
# Kia Hilux (Hilux is a Toyota model; that pairing is nonsense).
MAKE_WORDS = {"كيا": "Kia", "تويوتا": "Toyota", "تايوتا": "Toyota",
              "هونداي": "Hyundai", "هيونداي": "Hyundai", "نيسان": "Nissan",
              "متسوبيشي": "Mitsubishi", "ميتسوبيشي": "Mitsubishi"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    db.init()
    conn = db.connect()
    rows = conn.execute(
        "SELECT id, post_id, description, make, model FROM vehicles "
        "WHERE description LIKE ? AND make IN (%s)"
        % ",".join("?" * len(SEDAN_MAKES)),
        (f"%{BODY}%", *SEDAN_MAKES)).fetchall()

    BODY_WORDS = ("مضلع", "شريحة", "بوكس", "حافلة", "لوري", "قلاب", "ركشة",
                  "امجاد", "صالون")
    targets, already = [], 0
    for r in rows:
        d = normalize_ar(r["description"] or "")
        if any(normalize_ar(n) in d for n in NAMED):
            continue                      # a real model is named — leave it
        # Already repaired: the model IS a body type, not an invented sedan
        # name. Without this the script re-flags its own output forever, so
        # every later run looks like a fresh regression and the signal is lost.
        if any(b in (r["model"] or "") for b in BODY_WORDS):
            already += 1
            continue
        targets.append(r)

    print(f"rows with {BODY} and a sedan make : {len(rows)}")
    print(f"  named model present (correct)  : {len(rows) - len(targets) - already}")
    print(f"  already repaired               : {already}")
    print(f"  standalone body type (to fix)  : {len(targets)}")
    for r in targets[:8]:
        print(f"     post {r['post_id']}: {r['description'][:45]!r} "
              f"-> {r['make']} {r['model']}")

    if a.dry_run or not targets:
        print("\ndry run — nothing written" if a.dry_run else "\nnothing to fix")
        return

    with conn:
        for r in targets:
            conn.execute(
                "UPDATE vehicles SET make = NULL, model = ? WHERE id = ?",
                (BODY_EN, r["id"]))
    print(f"\nrepaired {len(targets)} rows -> make NULL, model {BODY_EN!r}")


if __name__ == "__main__":
    main()
