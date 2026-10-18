"""Deploy site/deploy/ to the Netlify project that serves mycarsd.online.

Browser upload is capped at 10MB and the payload is bigger, so this uses the
documented file-digest flow:
  1. POST the sha1 of every file -> Netlify replies with which ones it needs
  2. PUT each required file body
A deploy publishes ONLY the files listed here, so index.html, core.bin and
detail.bin must all be present or the site is left dataless.

Only files on scripts/deploy_allowlist.py may be published: a backup left in
site/deploy once went public for a minute. Anything else aborts the deploy.

    python scripts\\netlify_deploy.py            # deploy
    python scripts\\netlify_deploy.py --dry-run  # show what would be sent

Site id: config.yaml `netlify.site_id` (or NETLIFY_SITE_ID). Token:
NETLIFY_AUTH_TOKEN or site/netlify_token.txt (any letter case).
"""
import hashlib
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import requests

from carhunt.config import PROJECT_ROOT, load_config
from deploy_allowlist import ALLOW, REQUIRED

API = "https://api.netlify.com/api/v1"
DEPLOY_DIR = PROJECT_ROOT / "site" / "deploy"


def site_id() -> str:
    sid = ((load_config().get("netlify") or {}).get("site_id")
           or os.environ.get("NETLIFY_SITE_ID") or "").strip()
    if not sid:
        sys.exit("No Netlify site id: set netlify.site_id in config.yaml or NETLIFY_SITE_ID.")
    return sid


def token() -> str:
    tok = os.environ.get("NETLIFY_AUTH_TOKEN", "").strip()
    if tok:
        return tok
    for p in sorted((PROJECT_ROOT / "site").glob("[Nn]etlify_token.txt")):
        tok = p.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    sys.exit("No Netlify token: set NETLIFY_AUTH_TOKEN or create site/netlify_token.txt.")


def stage() -> dict:
    files = {}
    unknown = []
    for f in sorted(DEPLOY_DIR.iterdir()):
        if not f.is_file():
            unknown.append(f.name + "/")
            continue
        if f.name not in ALLOW:
            unknown.append(f.name)
            continue
        body = f.read_bytes()
        files["/" + f.name] = (hashlib.sha1(body).hexdigest(), body)
    if unknown:
        sys.exit(f"REFUSED: site/deploy contains files not on the allow-list: {unknown}. "
                 f"Move them to site/backups/ (never publish them).")
    missing = [r for r in REQUIRED if "/" + r not in files]
    if missing:
        sys.exit(f"REFUSED: required files missing from site/deploy: {missing}")
    return files


def main() -> None:
    dry = "--dry-run" in sys.argv
    files = stage()
    print("staging:")
    for path, (sha, body) in files.items():
        print(f"  {path:<16} {len(body):>10,} bytes  sha1 {sha[:12]}")
    if dry:
        print("\n--dry-run: nothing sent")
        return

    sid, H = site_id(), {"Authorization": f"Bearer {token()}"}
    r = requests.post(f"{API}/sites/{sid}/deploys", headers=H, timeout=120,
                      json={"files": {p: s for p, (s, _) in files.items()}, "draft": False})
    r.raise_for_status()
    dep = r.json()
    deploy_id, required = dep["id"], dep.get("required") or []
    print(f"\ndeploy {deploy_id} created, state={dep.get('state')}")
    print(f"netlify needs {len(required)} file(s) uploaded")

    by_sha = {s: (p, b) for p, (s, b) in files.items()}
    for sha in required:
        path, body = by_sha[sha]
        up = requests.put(f"{API}/deploys/{deploy_id}/files{path}",
                          headers={**H, "Content-Type": "application/octet-stream"},
                          data=body, timeout=600)
        up.raise_for_status()
        print(f"  uploaded {path} ({len(body):,} bytes)")

    for _ in range(60):
        st = requests.get(f"{API}/deploys/{deploy_id}", headers=H, timeout=60).json()
        state = st.get("state")
        if state in ("ready", "error"):
            print(f"\nfinal state: {state}")
            print("url:", st.get("deploy_ssl_url") or st.get("deploy_url"))
            if state == "error":
                print("message:", st.get("error_message"))
                sys.exit(1)
            (PROJECT_ROOT / "data" / "last_deploy.json").write_text(
                json.dumps({"id": deploy_id, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "files": {p: s for p, (s, _) in files.items()}}, indent=1),
                encoding="utf-8")
            return
        time.sleep(5)
    print("\nstill processing after 5 minutes; check the Netlify Deploys page")


if __name__ == "__main__":
    main()
