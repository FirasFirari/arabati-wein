"""Check the LIVE site (or a draft deploy) after a deploy. Exits non-zero on any failure.

    python scripts\\verify_deploy.py
    python scripts\\verify_deploy.py --base https://<deploy-id>--mycarsd.netlify.app

netlify_deploy.py runs this against the DRAFT before publishing it, then
against the live site. The payloads are also checked the way visitors get
them -- decompressed -- by carhunt/publish_guard.py.

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

from carhunt import publish_guard
from carhunt.config import PROJECT_ROOT

BASE = "https://mycarsd.online"
DEPLOY = PROJECT_ROOT / "site" / "deploy"
failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    failures += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {label}{(' — ' + detail) if detail else ''}")


def fetch(url: str, **kw):
    """GET that survives dropped connections by RESUMING (HTTP Range).

    2026-11-07: Netlify's draft URLs cut every connection after ~60-75 s. On a
    slow uplink (~80 KB/s) the 9 MB detail.bin never finished -- three plain
    retries died at the same ~3 MB -- and the traceback refused a draft that
    was fine. Each drop now continues from the last byte received. The bytes
    are still compared with the local file afterwards, so a bad deploy still
    fails; only a dropped connection is forgiven.
    """
    import time
    headers = dict(kw.pop("headers", None) or {})
    data = bytearray()
    first = None
    drops = 0
    while True:
        h = dict(headers)
        if data:
            h["Range"] = f"bytes={len(data)}-"
        try:
            r = requests.get(url, headers=h, stream=True, **kw)
            if data and r.status_code != 206:      # range ignored: start over
                data.clear()
                first = None
            if first is None:
                first = r                          # status and headers of the whole
            for chunk in r.iter_content(65536):
                data.extend(chunk)
            first._content = bytes(data)
            first._content_consumed = True
            return first
        except (requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.Timeout) as e:
            drops += 1
            if drops > 15:
                raise
            print(f"     (connection dropped at {len(data):,} bytes of "
                  f"{url.rsplit('/', 1)[-1]}: {type(e).__name__}; resuming, {drops}/15)")
            time.sleep(min(10, 2 * drops))


def get(path: str, **kw):
    return fetch(f"{BASE}/{path}", timeout=90, headers={"Cache-Control": "no-cache"}, **kw)


def main() -> None:
    global BASE
    if "--base" in sys.argv:
        BASE = sys.argv[sys.argv.index("--base") + 1].rstrip("/")
    print(f"verifying {BASE}")
    r = get("index.html")
    check("index.html 200", r.status_code == 200, str(r.status_code))
    html = r.text
    check("watch form present", bool(re.search(r"name=['\"]watch['\"]", html)) and "form-name" in html)
    # Netlify's pretty-URL processing rewrites href="/x.html" to href='/x'
    # (single quotes, no extension), so accept either quote style, an optional
    # host, and an optional .html or trailing slash.
    link = re.compile(r"""href=['"](?:https?://[^/'"]+)?/(support|privacy|stats)(?:\.html|/)?['"]""")
    check("footer links to support/privacy/stats",
          {m.group(1) for m in link.finditer(html)} >= {"support", "privacy", "stats"})
    check("meta description present", 'name="description"' in html)

    for name in ("core.bin", "detail.bin"):
        local = DEPLOY / name
        m = re.search(rf"{name}\?v=([0-9a-f]{{12}})", html)
        live = get(f"{name}?v={m.group(1) if m else 'x'}")
        same = live.status_code == 200 and hashlib.sha1(live.content).hexdigest() == hashlib.sha1(local.read_bytes()).hexdigest()
        check(f"{name} served == local", same, f"{len(live.content):,} bytes")
        if live.status_code == 200:
            # what visitors actually receive, decompressed: forbidden numbers
            # and stray phone numbers (grep cannot see inside the gzip)
            found = publish_guard.scan_payload(name, live.content, publish_guard.forbidden_patterns())
            check(f"{name} passes publish_guard", not found, "; ".join(found))

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
    # the free static host the page loads the payloads from first
    m = re.search(r"const DATA_HOST = '([^']*)'", html)
    if m and m.group(1):
        for name in ("core.bin", "detail.bin"):
            v = re.search(rf"{name}\?v=([0-9a-f]{{12}})", html)
            hosted = f"{m.group(1)}{name.split('.')[0]}.{v.group(1) if v else 'x'}.bin"
            rr = fetch(hosted, timeout=300, headers={"Origin": "https://mycarsd.online"})
            same = rr.status_code == 200 and rr.content == (DEPLOY / name).read_bytes()
            cors = rr.headers.get("Access-Control-Allow-Origin") in ("*", "https://mycarsd.online")
            check(f"{name} on the data host == local, CORS ok", same and cors, hosted)

    for pretty in ("support", "privacy", "stats"):
        rr = get(pretty, allow_redirects=True)
        check(f"/{pretty} resolves", rr.status_code == 200 and "<html" in rr.text.lower(), str(rr.status_code))

    print()
    if failures:
        sys.exit(f"{failures} check(s) FAILED")
    print("live site verified")


if __name__ == "__main__":
    main()
