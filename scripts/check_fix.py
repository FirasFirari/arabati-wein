import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db

db.init()
c = db.connect()
r = c.execute("SELECT make, model, model_key, color, description FROM vehicles WHERE id=34829").fetchone()
print("record 34829 now:", dict(r))

print("\ncorolla records:", c.execute(
    "SELECT COUNT(*) FROM vehicles WHERE model_key='toyota corolla'").fetchone()[0])
print("white corollas  :", c.execute(
    "SELECT COUNT(*) FROM vehicles WHERE model_key='toyota corolla' "
    "AND color LIKE '%white%'").fetchone()[0])
print("\nany شريحة still under a sedan key:", c.execute(
    "SELECT COUNT(*) FROM vehicles WHERE (description LIKE '%شريح%' OR model LIKE '%شريح%') "
    "AND model_key IN ('toyota corolla','toyota camry','hyundai accent','nissan sunny')"
).fetchone()[0])
