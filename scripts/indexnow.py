"""Tell Bing / Yandex / Naver (IndexNow) which URLs changed.

    python scripts\\indexnow.py            # submit every URL in site/deploy/sitemap.xml
    python scripts\\indexnow.py --dry-run  # print what would be sent

Also called by netlify_deploy.py after every successful deploy.

The key lives in site/static/indexnow-key.txt and is published verbatim at
https://mycarsd.online/indexnow-key.txt (that is how IndexNow proves ownership),
so it is not a secret, but the repo's pre-commit hook refuses any *key* file, so it
stays untracked (gitignored by name). A fresh clone generates a new one on first
run; a changed key is just a re-verification, nothing breaks.
"""
import pathlib
import re
import secrets
import sys

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
KEY_FILE = ROOT / "site" / "static" / "indexnow-key.txt"
SITEMAP = ROOT / "site" / "deploy" / "sitemap.xml"
HOST = "mycarsd.online"
ENDPOINT = "https://api.indexnow.org/indexnow"


def key() -> str:
    if not KEY_FILE.exists():
        KEY_FILE.write_text(secrets.token_hex(16), encoding="ascii")
        print(f"generated {KEY_FILE.relative_to(ROOT)} — rebuild + deploy before the first ping counts")
    return KEY_FILE.read_text(encoding="ascii").strip()


def urls() -> list[str]:
    if not SITEMAP.exists():
        return [f"https://{HOST}/"]
    return re.findall(r"<loc>(.*?)</loc>", SITEMAP.read_text(encoding="utf-8"))


def submit(dry: bool = False) -> None:
    k, u = key(), urls()
    body = {"host": HOST, "key": k, "keyLocation": f"https://{HOST}/indexnow-key.txt", "urlList": u}
    print(f"indexnow: {len(u)} url(s) -> {ENDPOINT}")
    if dry:
        for x in u:
            print("  ", x)
        return
    r = requests.post(ENDPOINT, json=body, timeout=30,
                      headers={"Content-Type": "application/json; charset=utf-8"})
    # 200 = ok, 202 = accepted (key not yet validated). Anything else is a real problem.
    print(f"indexnow: HTTP {r.status_code} {r.text[:120]!r}")
    if r.status_code not in (200, 202):
        raise RuntimeError(f"IndexNow refused: {r.status_code}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    submit(dry="--dry-run" in sys.argv)
