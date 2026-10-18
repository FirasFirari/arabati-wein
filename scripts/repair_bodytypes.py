"""Re-derive model_key for records whose text names a BODY TYPE.

A van described as "شريحه بيضا" was being filed as a Toyota Corolla, which
pollutes exactly the search the owner cares about. The alias table now knows
these words, so recompute the key from the record's own wording — and where
the stored model contradicts the wording, correct that too.
"""
import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db
from carhunt.aliases import model_keys_in, normalize_ar

BODY_KEYS = {"van/minibus (شريحة)", "pickup (بوكس)", "bus (حافلة)",
             "truck (لوري)", "rickshaw"}
SEDANS = {"toyota corolla", "toyota camry", "toyota corona", "toyota cressida",
          "hyundai accent", "hyundai avante", "hyundai sonata", "kia cerato",
          "nissan sunny", "mitsubishi lancer"}

db.init()
conn = db.connect()
rows = conn.execute(
    "SELECT id, make, model, description, model_key FROM vehicles").fetchall()

fixed = sedan_conflicts = 0
for r in rows:
    blob = f"{r['make'] or ''} {r['model'] or ''} {r['description'] or ''}"
    keys = model_keys_in(blob)
    body = next((k for k in keys if k in BODY_KEYS), None)
    if not body:
        continue
    # the wording names a body type; if it is currently filed as a sedan or
    # has no key at all, the body type is the honest classification
    if r["model_key"] in SEDANS:
        sedan_conflicts += 1
    if r["model_key"] != body:
        conn.execute("UPDATE vehicles SET model_key = ? WHERE id = ?",
                     (body, r["id"]))
        fixed += 1
conn.commit()
print(f"re-keyed {fixed} records to a body type "
      f"({sedan_conflicts} had been filed as sedans)")

print("\nrecords now keyed as a body type:")
for r in conn.execute(
        "SELECT model_key, COUNT(*) c FROM vehicles WHERE model_key IN "
        "('van/minibus (شريحة)','pickup (بوكس)','bus (حافلة)','truck (لوري)','rickshaw') "
        "GROUP BY model_key ORDER BY c DESC"):
    print(f"   {r['model_key']:24s} {r['c']}")

left = conn.execute(
    "SELECT COUNT(*) FROM vehicles WHERE (description LIKE '%شريح%' OR model LIKE '%شريح%') "
    "AND model_key = 'toyota corolla'").fetchone()[0]
print(f"\nشريحة records still filed as Corolla: {left}")
