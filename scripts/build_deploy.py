"""Assemble site/deploy from the export and site/static, safely.

    python scripts\\build_deploy.py            # build
    python scripts\\build_deploy.py --check    # verify only

Steps:
  1. require site/arabati-wein.html, core.bin and detail.bin, and check that the
     bins' sha1[:12] match the ?v= cache-busters inside the HTML (same export run)
  2. regenerate stats.json / stats.html (scripts/publish_stats.py, read-only DB)
  3. copy: html -> site/deploy/index.html, the bins, and every allow-listed file
     from site/static (filling __CONTACT__, __BUILD_DATE__ and __CLARITY__ tokens)
  4. write sitemap.xml with today's lastmod
  5. refuse to finish if site/deploy holds anything not on the allow-list
     (never deletes; tells you what to move to site/backups/)
"""
import datetime
import hashlib
import pathlib
import re
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from carhunt.config import PROJECT_ROOT, load_config
from deploy_allowlist import ALLOW, REQUIRED

SITE = PROJECT_ROOT / "site"
STATIC = SITE / "static"
DEPLOY = SITE / "deploy"
BASE_URL = "https://mycarsd.online"
PAGES = ["", "guide.html", "scams.html", "yards.html", "en.html",
         "support.html", "privacy.html", "stats.html"]


def sha12(p: pathlib.Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()[:12]


def contact_text() -> str:
    cfg = load_config()
    email = ((cfg.get("notify") or {}).get("contact_email") or "").strip()
    return email or "Telegram: @ArabatiWeinBot (email address coming soon)"


def check_export() -> None:
    html = SITE / "arabati-wein.html"
    for f in (html, SITE / "core.bin", SITE / "detail.bin"):
        if not f.exists():
            sys.exit(f"missing {f} — run python -m carhunt.export_site first")
    text = html.read_text(encoding="utf-8")
    for name in ("core.bin", "detail.bin"):
        m = re.search(rf"{name}\?v=([0-9a-f]{{12}})", text)
        if not m:
            sys.exit(f"{html.name} has no ?v= cache-buster for {name}")
        if m.group(1) != sha12(SITE / name):
            sys.exit(f"{name} is from a different export than {html.name} "
                     f"(html expects {m.group(1)}, file is {sha12(SITE / name)}). Re-export.")
    print("export consistent: html and bins are from the same run")


def write_sitemap(today: str) -> None:
    urls = "\n".join(
        f"  <url><loc>{BASE_URL}/{p}</loc><lastmod>{today}</lastmod>"
        f"<changefreq>{'daily' if p == '' else 'weekly'}</changefreq></url>" for p in PAGES)
    (DEPLOY / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + urls + "\n</urlset>\n",
        encoding="utf-8")


def main() -> None:
    check_only = "--check" in sys.argv
    check_export()
    if check_only:
        return
    import publish_stats
    publish_stats.main()

    DEPLOY.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    contact = contact_text()
    from carhunt.export_site import clarity_snippet
    clarity = clarity_snippet()
    if not clarity:
        print("WARNING: no site/clarity_id.txt, content pages ship untracked")

    shutil.copyfile(SITE / "arabati-wein.html", DEPLOY / "index.html")
    shutil.copyfile(SITE / "core.bin", DEPLOY / "core.bin")
    shutil.copyfile(SITE / "detail.bin", DEPLOY / "detail.bin")
    copied = ["index.html", "core.bin", "detail.bin"]
    for f in sorted(STATIC.iterdir()):
        if f.is_file() and f.name not in ALLOW and not f.name.endswith(".template.html"):
            print(f"WARNING: site/static/{f.name} is not on scripts/deploy_allowlist.py "
                  f"and will NOT be published")
        if not f.is_file() or f.name not in ALLOW or f.name == "sitemap.xml":
            continue
        if f.suffix in (".html", ".json", ".txt", ".xml", ".webmanifest") or f.name.startswith("_"):
            text = f.read_text(encoding="utf-8")
            text = text.replace("__CONTACT__", contact).replace("__BUILD_DATE__", today)
            # Clarity on every content page, from the same hostname-gated
            # snippet and gitignored id as index.html. Until 2026-11-04 only the
            # home page was tagged, so en/guide/yards/scams visits were
            # invisible and showed up only as utm_source=<page> click-throughs.
            text = text.replace("__CLARITY__", clarity)
            enc = "ascii" if f.name.startswith("_") else "utf-8"
            (DEPLOY / f.name).write_text(text, encoding=enc, newline="\n")
        else:
            shutil.copyfile(f, DEPLOY / f.name)
        copied.append(f.name)
    write_sitemap(today)
    copied.append("sitemap.xml")

    # An allow-listed file this build did not produce is a leftover from an
    # earlier one (a page since removed from site/static): netlify_deploy.py
    # publishes everything here, so it would stay public forever. Remove it.
    for p in DEPLOY.iterdir():
        if p.is_file() and p.name in ALLOW and p.name not in copied:
            p.unlink()
            print(f"removed stale {p.name} (not produced by this build)")

    leftovers = [p.name for p in DEPLOY.iterdir() if p.name not in ALLOW]
    print("site/deploy:")
    for p in sorted(DEPLOY.iterdir()):
        print(f"  {p.name:<16} {p.stat().st_size:>10,} bytes  {sha12(p) if p.is_file() else 'DIR'}")
    if leftovers:
        sys.exit(f"REFUSED: site/deploy holds files not on the allow-list: {leftovers}. "
                 f"Move them to site/backups/ and rerun. Nothing was deleted.")
    missing = [r for r in REQUIRED if not (DEPLOY / r).exists()]
    if missing:
        sys.exit(f"missing required files: {missing}")
    print(f"ok — {len(copied)} files ready. Next: python scripts\\netlify_deploy.py")


if __name__ == "__main__":
    main()
