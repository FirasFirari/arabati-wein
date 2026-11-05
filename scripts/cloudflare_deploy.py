"""Publish the two payloads to the free static host (Cloudflare Pages).

    python scripts\\cloudflare_deploy.py            # upload site/deploy's bins
    python scripts\\cloudflare_deploy.py --check    # verify what is live there

Why: core.bin + detail.bin are ~99% of the bytes mycarsd.online serves
(measured 2026-11-04: 484 MB in 24 h). Netlify's plan bills bandwidth in
credits, and running out of credits pauses the whole site -- it happened on
2026-08-20 and 2026-09-22. Cloudflare Pages does not meter static requests.
The page (export_site.py) loads them from `cloudflare.data_host` first and
falls back to the same-origin copy, which netlify_deploy.py keeps shipping.

Files are published under CONTENT-HASHED names (core.<sha12>.bin), so a
visitor holding an older page still finds the bytes it asks for and a new
build can never be mixed with an old one -- two hosts cannot switch over
atomically, and the force-cache staleness bug of 2026-09-01 must not return.
The previous build's pair is published again alongside the new one.

Config (config.yaml): cloudflare.account_id, cloudflare.pages_project,
cloudflare.data_host. Token: site/cloudflare_token.txt (Cloudflare Pages: Edit)
or CLOUDFLARE_API_TOKEN. Uses the official `wrangler` CLI.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import requests  # noqa: E402

from carhunt import publish_guard  # noqa: E402
from carhunt.config import DATA_DIR, load_config  # noqa: E402

DEPLOY = ROOT / "site" / "deploy"
STAGE = ROOT / "site" / "cfdata"            # gitignored: rebuilt every run
KEEP = DATA_DIR / "cf_published"           # the pairs already published
HEADERS = """/*
  Access-Control-Allow-Origin: *
  Cache-Control: public, max-age=31536000, immutable
  X-Content-Type-Options: nosniff
  Content-Type: application/octet-stream
/
  Cache-Control: no-cache
  Content-Type: text/html; charset=utf-8
/index.html
  Cache-Control: no-cache
  Content-Type: text/html; charset=utf-8
"""
INDEX = ("<!doctype html><meta charset=utf-8><title>mycarsd data</title>"
         "<p>Data files for <a href='https://mycarsd.online'>mycarsd.online</a>.</p>\n")


def settings() -> dict:
    cf = (load_config() or {}).get("cloudflare") or {}
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not token:
        f = ROOT / "site" / "cloudflare_token.txt"
        token = f.read_text(encoding="utf-8").strip() if f.exists() else ""
    return {"account": str(cf.get("account_id") or ""), "project": str(cf.get("pages_project") or ""),
            "host": str(cf.get("data_host") or "").rstrip("/") + "/", "token": token}


def current_pairs() -> dict[str, str]:
    """{'core.<v>.bin': path, 'detail.<v>.bin': path} for the build in site/deploy."""
    html = (DEPLOY / "index.html").read_text(encoding="utf-8")
    out = {}
    for name in ("core", "detail"):
        m = re.search(rf"{name}\.bin\?v=([0-9a-f]{{12}})", html)
        if not m:
            sys.exit(f"index.html has no ?v= for {name}.bin")
        blob = DEPLOY / f"{name}.bin"
        if hashlib.sha1(blob.read_bytes()).hexdigest()[:12] != m.group(1):
            sys.exit(f"{name}.bin is not the build index.html embeds — rebuild site/deploy")
        out[f"{name}.{m.group(1)}.bin"] = blob
    return out


def stage(pairs: dict) -> list[str]:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    KEEP.mkdir(parents=True, exist_ok=True)
    for name, src in pairs.items():
        shutil.copyfile(src, KEEP / name)
    # this build plus the previous one: a visitor on the old page keeps working
    builds = sorted({p.name.split(".")[1] for p in KEEP.glob("core.*.bin")},
                    key=lambda v: (KEEP / f"core.{v}.bin").stat().st_mtime)[-2:]
    names = [f"{kind}.{v}.bin" for v in builds for kind in ("core", "detail")
             if (KEEP / f"{kind}.{v}.bin").exists()]
    for stale in KEEP.glob("*.bin"):
        if stale.name not in names:
            stale.unlink()
    findings = []
    for n in names:
        shutil.copyfile(KEEP / n, STAGE / n)
        findings += publish_guard.scan_payload(n.replace(".", "_", 1), (STAGE / n).read_bytes(),
                                               publish_guard.forbidden_patterns())
    if findings:
        sys.exit("REFUSED by publish_guard:\n  " + "\n  ".join(findings))
    (STAGE / "_headers").write_text(HEADERS, encoding="ascii", newline="\n")
    (STAGE / "index.html").write_text(INDEX, encoding="utf-8")
    return names


def check(pairs: dict, cfg: dict) -> bool:
    ok = True
    for name, src in pairs.items():
        r = requests.get(cfg["host"] + name, timeout=300,
                         headers={"Origin": "https://mycarsd.online", "Cache-Control": "no-cache"})
        same = r.status_code == 200 and r.content == src.read_bytes()
        cors = r.headers.get("Access-Control-Allow-Origin") in ("*", "https://mycarsd.online")
        print(f"{'ok  ' if same and cors else 'FAIL'} {cfg['host']}{name} "
              f"({len(r.content):,} bytes, status {r.status_code}, CORS {'yes' if cors else 'NO'})")
        ok &= same and cors
    return ok


def main() -> int:
    cfg = settings()
    if not (cfg["account"] and cfg["project"] and cfg["token"] and cfg["host"].startswith("https://")):
        print("cloudflare: not configured (config cloudflare.* or token missing) — skipped")
        return 2
    pairs = current_pairs()
    if "--check" in sys.argv:
        return 0 if check(pairs, cfg) else 1
    names = stage(pairs)
    print(f"cloudflare: publishing {names}")
    env = {**os.environ, "CLOUDFLARE_API_TOKEN": cfg["token"], "CLOUDFLARE_ACCOUNT_ID": cfg["account"]}
    wrangler = shutil.which("wrangler") or shutil.which("wrangler.cmd")
    if not wrangler:
        sys.exit("wrangler is not installed (npm install -g wrangler)")
    r = subprocess.run([wrangler, "pages", "deploy", str(STAGE), "--project-name", cfg["project"],
                        "--branch", "main", "--commit-dirty=true"],
                       env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = "\n".join(line for line in (r.stdout + r.stderr).splitlines()[-6:] if "token" not in line.lower())
    print(tail)
    if r.returncode != 0:
        print("cloudflare: wrangler failed")
        return 1
    return 0 if check(pairs, cfg) else 1


if __name__ == "__main__":
    sys.exit(main())
