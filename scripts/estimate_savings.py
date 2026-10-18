import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db
from carhunt.config import load_config
from carhunt.prefilter import identifier_hit, is_relevant

cfg = load_config()
car = cfg["car"]
db.init()
conn = db.connect()

rows = conn.execute(
    "SELECT p.id, p.source, p.text, "
    "(SELECT COUNT(*) FROM media m WHERE m.post_id = p.id) AS nmedia "
    "FROM posts p WHERE p.score IS NULL").fetchall()

total = len(rows)
skipped = hits = 0
per_source_skip = {}
hashes = {}
dupes = 0
for r in rows:
    hit = identifier_hit(r["text"], car)
    if hit:
        hits += 1
        print(f"  !! IDENTIFIER HIT post {r['id']} [{r['source']}]: {hit}")
        continue
    media = ["x"] * r["nmedia"]
    if not is_relevant(r["text"], media):
        skipped += 1
        per_source_skip[r["source"]] = per_source_skip.get(r["source"], 0) + 1
        continue
    h = db.content_hash(r["text"])
    if h:
        if h in hashes:
            dupes += 1
            continue
        hashes[h] = r["id"]

billable = total - skipped - hits - dupes
print(f"\nqueued posts:            {total}")
print(f"identifier hits (free):  {hits}")
print(f"prefiltered out (free):  {skipped}")
print(f"duplicate content (free):{dupes}")
print(f"still needs an API call: {billable}")
if total:
    print(f"=> {100 * (skipped + dupes + hits) / total:.1f}% of calls avoided")
print("\ntop prefiltered sources:")
for src, n in sorted(per_source_skip.items(), key=lambda kv: -kv[1])[:6]:
    print(f"   {src}: {n}")
