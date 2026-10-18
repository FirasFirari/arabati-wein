import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db
from carhunt.aliases import model_key_for

db.init()
conn = db.connect()
updated = 0
for row in conn.execute("SELECT id, make, model, description FROM vehicles").fetchall():
    key = model_key_for(row["make"], row["model"], row["description"])
    if key:
        conn.execute("UPDATE vehicles SET model_key = ? WHERE id = ?", (key, row["id"]))
        updated += 1
conn.commit()
total = conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]
print(f"model_key set on {updated}/{total} vehicles")
for row in conn.execute("SELECT model_key, COUNT(*) FROM vehicles WHERE model_key IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 12"):
    print("  ", row[0], row[1])
