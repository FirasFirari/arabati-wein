import re
html = open(r"C:\Users\User\Desktop\Car Hunt\site\arabati-wein.html", encoding="utf-8").read()

print("placeholders substituted:")
for t in ["__STATS__", "__UPDATED__", "__DATA__", "__ALIASES__"]:
    print(f"   {t:14s}", "LEFTOVER!" if t in html else "ok")

print("\nelements present:")
for t in ['id="coverage"', "sources-list", "prettySource", "const STATS",
          "chip warn", "passesFilters", "f-reset"]:
    print(f"   {t:18s}", "ok" if t in html else "MISSING")

m = re.search(r"const STATS = (\{.*?\});", html, re.S)
if m:
    import json
    stats = json.loads(m.group(1).replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&"))
    print(f"\nstats embedded: {stats['analyzed']} analyzed, {stats['posts']} posts, "
          f"{stats['vehicles']} vehicles, {len(stats['sources'])} sources, {stats['photos']} photos")
    print("top sources:")
    for s in stats["sources"][:6]:
        print(f"   {s['name']:38s} {s['posts']}")
