"""Where a record is keyed as a body type but still displays a sedan name,
correct the visible make/model so the card cannot contradict itself.
"""
import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db

LABEL = {
    "van/minibus (شريحة)": "van / minibus (شريحة)",
    "pickup (بوكس)": "pickup (بوكس)",
    "bus (حافلة)": "bus (حافلة)",
    "truck (لوري)": "truck (لوري)",
    "rickshaw": "rickshaw (ركشة)",
}
SEDANS = {"corolla", "camry", "corona", "cressida", "accent", "avante",
          "sonata", "cerato", "sunny", "lancer", "elantra", "picanto"}

db.init()
conn = db.connect()
rows = conn.execute(
    "SELECT id, make, model, model_key FROM vehicles "
    "WHERE model_key IN ({})".format(",".join("?" * len(LABEL))),
    tuple(LABEL)).fetchall()

fixed = 0
for r in rows:
    model = (r["model"] or "").strip().lower()
    if not model:
        continue
    if any(s in model for s in SEDANS):
        # the visible model contradicts the body type the text actually names
        conn.execute("UPDATE vehicles SET model = ? WHERE id = ?",
                     (LABEL[r["model_key"]], r["id"]))
        fixed += 1
conn.commit()
print(f"corrected the displayed model on {fixed} records")

r = conn.execute("SELECT make, model, model_key, color, description "
                 "FROM vehicles WHERE id=34829").fetchone()
print("record 34829 now:", dict(r))
