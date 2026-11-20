"""Audit of carhunt/aliases.py against the registry. READ-ONLY. Re-run after ANY change to the table:
section (a2) lists words matched only through the consonant skeleton, which is where every
new key brings false look-alikes (برايد read as Prado, بترول as Patrol)."""
import re
import sqlite3
import sys
from collections import Counter, defaultdict

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from carhunt import aliases as A

conn = sqlite3.connect("file:data/carhunt.db?mode=ro", uri=True, timeout=60)
descs = Counter()
for (d,) in conn.execute("SELECT description FROM vehicles WHERE description IS NOT NULL"):
    descs[d] += 1
print("rows with a description:", sum(descs.values()), " distinct:", len(descs))

AR_WORD = re.compile(r"[ء-يٮ-ۓ]{2,}")
LAT_WORD = re.compile(r"[a-z][a-z0-9]{1,}")

# ---- (a) which alias / skeleton word produced each key ----------------------
exact_hits = Counter()                 # alias -> rows
skel_hits = defaultdict(Counter)       # key -> word -> rows
unmatched_first = Counter()            # first word of descriptions with no key
unmatched_any = Counter()
co = defaultdict(Counter)              # nickname -> co-occurring words
NICK = ["دفار", "حرجلي", "امجاد", "دبدوب", "سحليه", "منفوخه", "بومه", "مضلع", "جياد",
        "كلك", "قرش", "صالون", "شريحه", "بوكس", "حافله"]
latin_only = defaultdict(Counter)      # english alias -> example descriptions (no arabic alias)
for d, n in descs.items():
    norm = A.normalize_ar(d)
    found_exact = []
    for alias, key in A._ALIAS_EXACT.items():
        if A._find_word(norm, alias) != -1:
            exact_hits[alias] += n
            found_exact.append((alias, key))
    keys = {k for _, k in found_exact}
    for m in re.finditer(r"[؀-ۿ]{3,}", norm):
        w = m.group(0)
        if A._skeleton_stopped(w) or w in A.WEAK_ALIASES or w in A._ALIAS_EXACT:
            continue
        k = A._ALIAS_SKEL.get(A.skeleton(w))
        if k and not any(A._find_word(w, a) != -1 for a, kk in A._ALIAS_EXACT.items() if kk == k):
            skel_hits[k][w] += n
            keys.add(k)
    words = AR_WORD.findall(norm)
    for nick in NICK:
        if nick in words:
            for w in set(words):
                if w != nick:
                    co[nick][w] += n
    if not keys:
        if words:
            unmatched_first[words[0]] += n
        for w in set(words):
            unmatched_any[w] += n
        for w in set(LAT_WORD.findall(norm)):
            unmatched_any[w] += n
    ar_alias = [a for a, _ in found_exact if "ء" <= a[0] <= "ي"]
    if found_exact and not ar_alias:
        for a, _ in found_exact:
            latin_only[a][d[:80]] += n

print("\n=== (a1) aliases that never or almost never occur (rows) ===")
for key, names in A.ALIASES.items():
    for name in names + [key]:
        a = A.normalize_ar(name)
        if exact_hits[a] < 20:
            print(f"  {exact_hits[a]:6d}  {name!r:28s} -> {key}")

print("\n=== (a2) words matched only through the consonant skeleton, per key ===")
for key in A.ALIASES:
    if skel_hits[key]:
        top = ", ".join(f"{w}({n})" for w, n in skel_hits[key].most_common(9))
        print(f"  {key:22s} {sum(skel_hits[key].values()):7d}: {top}")

print("\n=== (b) what stands beside each nickname ===")
for nick in NICK:
    top = ", ".join(f"{w}({n})" for w, n in co[nick].most_common(22))
    print(f"  {nick} [{exact_hits.get(nick, 0)} rows]: {top}")

print("\n=== (c) most frequent FIRST word of descriptions that get no model ===")
print("  " + ", ".join(f"{w}({n})" for w, n in unmatched_first.most_common(110)))

print("\n=== (c2) most frequent words anywhere in descriptions with no model ===")
print("  " + ", ".join(f"{w}({n})" for w, n in unmatched_any.most_common(140)))

print("\n=== (d) English aliases that matched with no Arabic alias beside them ===")
for a in sorted(latin_only, key=lambda a: -sum(latin_only[a].values())):
    c = latin_only[a]
    print(f"  {a!r} {sum(c.values())}: " + " || ".join(f"{d}" for d, _ in c.most_common(3)))

print("\n=== (e) skeleton collisions inside the table ===")
sk = defaultdict(set)
for key, names in A.ALIASES.items():
    for name in names + [key]:
        s = A.skeleton(name)
        if len(s) >= 3:
            sk[s].add((key, name))
for s, items in sk.items():
    if len({k for k, _ in items}) > 1:
        print("  ", s, sorted(items))

print("\n=== (f) make/model fields built from a body-type key ===")
for row in conn.execute(
        "SELECT make, model, COUNT(*) FROM vehicles WHERE make IN "
        "('Pickup','Van/Minibus','Bus','Truck','Rickshaw','Giad','Byd','Geely') "
        "OR model LIKE '(%' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 14"):
    print("  ", row)
print("\n=== (g) rows per key ===")
for row in conn.execute("SELECT model_key, COUNT(*) FROM vehicles GROUP BY 1 ORDER BY 2 DESC"):
    print("  ", row)
