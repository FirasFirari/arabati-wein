import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db

db.init()
conn = db.connect()
rows = conn.execute("""
    SELECT p.id, p.source, LENGTH(COALESCE(p.text,'')) AS tlen,
           (SELECT COUNT(*) FROM media m WHERE m.post_id = p.id) AS nmedia
    FROM posts p WHERE p.score IS NULL""").fetchall()

text_only = [r for r in rows if r["nmedia"] == 0]
with_img = [r for r in rows if r["nmedia"] > 0]
chars = sum(r["tlen"] for r in rows)
imgs = sum(min(r["nmedia"], 2) for r in with_img)   # screen tier sends max 2

print(f"queued: {len(rows)}")
print(f"  text-only : {len(text_only)}")
print(f"  with photo: {len(with_img)}  (screen tier would send {imgs} images)")
print(f"  total text: {chars/1000:.0f}k chars (~{chars/3500:.0f}k tokens)")

print("\nby source:")
for r in conn.execute("""
    SELECT source, COUNT(*) c FROM posts WHERE score IS NULL
    GROUP BY source ORDER BY c DESC"""):
    print(f"   {r['source']:32s} {r['c']}")

# rough batch-price estimate for the cheap screen tier
in_tok = chars / 3500 * 1000 + imgs * 1200      # ~1.2k tokens per image
out_tok = len(rows) * 700
haiku_in, haiku_out = 1.00, 5.00                 # $ per 1M tokens
cost = (in_tok / 1e6 * haiku_in + out_tok / 1e6 * haiku_out) * 0.5  # batch = 50%
print(f"\nrough screen-tier cost via Batches API: ${cost:.2f}")
print("(plus a small deep-pass cost for whatever the screen escalates)")
