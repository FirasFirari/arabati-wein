import re
import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import db
from carhunt.normalize import norm_chassis

db.init()
conn = db.connect()
row = conn.execute("SELECT text, url FROM posts WHERE id = 1800").fetchone()
text = row["text"] or ""
print("url:", row["url"], "\nlen:", len(text))

# literal occurrence in the raw text?
for m in re.finditer("113335", text):
    print(f"\n--- LITERAL match at {m.start()} ---")
    print(repr(text[max(0, m.start() - 120): m.start() + 120]))

# where the stripped-alphanumeric form matches (may span entry boundaries)
flat = norm_chassis(text)
idx = flat.find("113335")
print(f"\nflattened index: {idx}")
if idx != -1:
    # map flattened position back to raw text
    count = 0
    raw_pos = None
    for i, ch in enumerate(text):
        if norm_chassis(ch):
            if count == idx:
                raw_pos = i
                break
            count += 1
    if raw_pos is not None:
        print("--- RAW CONTEXT around flattened match ---")
        print(repr(text[max(0, raw_pos - 150): raw_pos + 150]))
