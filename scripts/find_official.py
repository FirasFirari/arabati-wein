import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db

TERMS = ["حوش المرور", "دائرة مرور", "الإدارة العامة للمرور", "إقرار مشفوع",
         "لجنة سحب", "المركبات المنهوبة", "الوالي", "الشرطة", "النيابة",
         "بطاقة قومية", "إثبات الملكية", "مجانا", "مجاناً"]

db.init()
conn = db.connect()
seen = {}
for term in TERMS:
    rows = conn.execute(
        "SELECT id, source, substr(text, 1, 200) FROM posts WHERE text LIKE ? LIMIT 25",
        (f"%{term}%",)).fetchall()
    for r in rows:
        seen.setdefault(r[0], (r[1], r[2], []))[2].append(term)

print(f"{len(seen)} posts mention official-recovery language\n")
for pid, (src, snippet, terms) in sorted(seen.items())[:20]:
    print(f"--- post {pid} [{src}] terms: {', '.join(terms[:4])}")
    print(snippet.replace("\n", " | ")[:190])
