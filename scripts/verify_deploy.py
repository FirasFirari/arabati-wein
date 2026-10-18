"""Check the LIVE site after a deploy. Exits non-zero on any failure.

    python scripts\\verify_deploy.py

Checks: the watch form survived (name='watch' + hidden form-name), the two
payloads served match the local files byte for byte, every public page answers
200, pretty URLs resolve, stats.json is fresh, and the JSON-LD on the support
page parses.
"""
import datetime
import hashlib
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import requests

from carhunt.config import PROJECT_ROOT

BASE = "https://mycarsd.online"
DEPLOY = PROJECT_ROOT / "site" / "deploy"
failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    failures += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {label}{(' — ' + detail) if detail else ''}")


def get(path: str, **kw):
    return requests.get(f"{BASE}/{path}", timeout=90, headers={"Cache-Control": "no-cache"}, **kw)


def main() -> None:
    r = get("index.html")
    check("index.html 200", r.status_code == 200, str(r.status_code))
    html = r.text
    check("watch form present", bool(re.search(r"name=['\"]watch['\"]", html)) and "form-name" in html)
    # Netlify's pretty-URL processing may rewrite href="/x.html" to href="/x"
    check("footer links to support/privacy/stats",
          all(re.search(rf'href="/{x}(\.html)?"', html) for x in ("support", "privacy", "stats")))
    check("meta description present", 'name="description"' in html)

    for name in ("core.bin", "detail.bin"):
        local = DEPLOY / name
        m = re.search(rf"{name}\?v=([0-9a-f]{{12}})", html)
        live = get(f"{name}?v={m.group(1) if m else 'x'}")
        same = live.status_code == 200 and hashlib.sha1(live.content).hexdigest() == hashlib.sha1(local.read_bytes()).hexdigest()
        check(f"{name} served == local", same, f"{len(live.content):,} bytes")

    for page in ("support.html", "privacy.html", "stats.html", "robots.txt", "sitemap.xml",
                 "funding.json", "llms.txt", "stats.json"):
        rr = get(page)
        check(f"{page} 200", rr.status_code == 200, str(rr.status_code))
        if page == "support.html" and rr.status_code == 200:
            ld = re.findall(r'<script type="application/ld\+json">(.*?)</script>', rr.text, re.S)
            parsed = True
            try:
                for block in ld:
                    json.loads(block)
            except Exception:
                parsed = False
            check("support.html JSON-LD parses", bool(ld) and parsed, f"{len(ld)} block(s)")
            check("support.html has no unfilled tokens", "__CONTACT__" not in rr.text and "__BUILD_DATE__" not in rr.text)
        if page == "stats.json" and rr.status_code == 200:
            gen = (rr.json().get("generated_at") or "")[:10]
            try:
                age = (datetime.date.today() - datetime.date.fromisoformat(gen)).days
            except ValueError:
                age = 999
            check("stats.json generated within 2 days", age <= 2, gen)
    for pretty in ("support", "privacy", "stats"):
        rr = get(pretty, allow_redirects=True)
        check(f"/{pretty} resolves", rr.status_code == 200 and "<html" in rr.text.lower(), str(rr.status_code))

    print()
    if failures:
        sys.exit(f"{failures} check(s) FAILED")
    print("live site verified")


if __name__ == "__main__":
    main()
