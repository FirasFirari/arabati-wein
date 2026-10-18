import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db
from carhunt.config import load_config
from carhunt.prefilter import is_relevant

cfg = load_config()
general = [s.lower() for s in (cfg.get("general_sources") or [])]
db.init()
conn = db.connect()

rows = conn.execute(
    "SELECT p.id, p.source, p.text, "
    "(SELECT COUNT(*) FROM media m WHERE m.post_id = p.id) AS nmedia "
    "FROM posts p WHERE p.score IS NULL").fetchall()

before = after = 0
saved_by_source = {}
for r in rows:
    media = ["x"] * r["nmedia"]
    src = (r["source"] or "").lower()
    strict = any(g in src for g in general)
    was = is_relevant(r["text"], media, strict=False)
    now = is_relevant(r["text"], media, strict=strict)
    before += 1 if was else 0
    after += 1 if now else 0
    if was and not now:
        saved_by_source[r["source"]] = saved_by_source.get(r["source"], 0) + 1

print(f"queued posts: {len(rows)}")
print(f"billable before source policy: {before}")
print(f"billable after  source policy: {after}")
print(f"=> {before - after} further calls avoided")
for s, n in sorted(saved_by_source.items(), key=lambda kv: -kv[1]):
    print(f"   {s}: {n}")
