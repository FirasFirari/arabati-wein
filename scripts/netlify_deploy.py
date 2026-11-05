"""Deploy site/deploy/ to the Netlify project that serves mycarsd.online.

Browser upload is capped at 10MB and the payload is bigger, so this uses the
documented file-digest flow:
  1. POST the sha1 of every file -> Netlify replies with which ones it needs
  2. PUT each required file body
A deploy publishes ONLY the files listed here, so index.html, core.bin and
detail.bin must all be present or the site is left dataless. The same goes for
FUNCTIONS: every *.js in site/functions/ is zipped and sent with each deploy
(`functions` digest = sha256 of the zip, then PUT .../functions/<name>), and a
deploy that listed none would remove them. submission-created.js is the
instant Telegram alert for new watch registrations - see that file.

Only files on scripts/deploy_allowlist.py may be published: a backup left in
site/deploy once went public for a minute. Anything else aborts the deploy.

    python scripts\\netlify_deploy.py                 # draft -> verify -> publish
    python scripts\\netlify_deploy.py --dry-run       # show what would be sent
    python scripts\\netlify_deploy.py --allow-shrink  # accept >5% fewer vehicles
    python scripts\\netlify_deploy.py --rollback [ID] # re-publish the previous deploy

Since 2026-11-04 every deploy is a DRAFT first: verify_deploy.py checks it at
its own URL, and only then is it published. A build with more than 5% fewer
vehicles than the live one is refused, and each deploy records its git commit
and the deploy it replaced in data/deploy_history.jsonl, which --rollback uses.

Site id: config.yaml `netlify.site_id` (or NETLIFY_SITE_ID). Token:
NETLIFY_AUTH_TOKEN or site/netlify_token.txt (any letter case).
"""
import hashlib
import io
import json
import os
import pathlib
import sys
import time
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import requests

from carhunt import publish_guard
from carhunt.config import PROJECT_ROOT, load_config
from deploy_allowlist import ALLOW, REQUIRED

API = "https://api.netlify.com/api/v1"
DEPLOY_DIR = PROJECT_ROOT / "site" / "deploy"
FUNCTIONS_DIR = PROJECT_ROOT / "site" / "functions"


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
    # What ships is checked as visitors receive it: payloads decompressed, HTML
    # as built. A number the owner ruled out stayed live inside detail.bin
    # because grep over the source cannot see into a gzip.
    findings = publish_guard.scan_dir(DEPLOY_DIR)
    if findings:
        sys.exit("REFUSED by publish_guard:\n  " + "\n  ".join(findings))
    return files


def stage_functions() -> dict:
    """name -> (sha256, zip bytes) for every site/functions/*.js.

    Netlify's file-digest deploy takes functions as zips: the zip must hold the
    entry file at its root, named after the function. Zip metadata is pinned
    (fixed timestamp, no extra fields) so an unchanged source gives an unchanged
    digest and Netlify can skip the upload.
    """
    out = {}
    if not FUNCTIONS_DIR.is_dir():
        return out
    for f in sorted(FUNCTIONS_DIR.glob("*.js")):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            info = zipfile.ZipInfo(f.name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, f.read_bytes())
        body = buf.getvalue()
        out[f.stem] = (hashlib.sha256(body).hexdigest(), body)
    return out


HISTORY = PROJECT_ROOT / "data" / "deploy_history.jsonl"
LAST = PROJECT_ROOT / "data" / "last_deploy.json"
SHRINK_FLOOR = 0.95


def dossier_count(files: dict) -> int | None:
    """Vehicles in the core payload about to ship (None if unreadable)."""
    try:
        import gzip
        core = json.loads(gzip.decompress(files["/core.bin"][1]))
        return len(core["ord"])
    except Exception:
        return None


def check_shrink(dossiers: int | None, allow: bool) -> None:
    """Refuse a build that lost more than 5% of the vehicles of the live one.

    On 2026-08-26 the export silently wrote nothing for days; an empty or
    half-written build passes every hash check, because it is consistent with
    itself. Only a comparison with what is live catches it."""
    if dossiers is None:
        sys.exit("REFUSED: cannot read the vehicle count from core.bin")
    try:
        previous = json.loads(LAST.read_text(encoding="utf-8")).get("dossiers")
    except (OSError, ValueError):
        previous = None
    if previous and dossiers < previous * SHRINK_FLOOR and not allow:
        sys.exit(f"REFUSED: this build has {dossiers:,} vehicles, the live one {previous:,} "
                 f"(-{1 - dossiers / previous:.0%}). If that is intended, rerun with --allow-shrink.")
    print(f"vehicles: {dossiers:,}" + (f" (live: {previous:,})" if previous else ""))


def git_state() -> dict:
    import subprocess
    run = lambda *a: subprocess.run(["git", *a], cwd=PROJECT_ROOT, capture_output=True,  # noqa: E731
                                    text=True).stdout.strip()
    return {"git": run("rev-parse", "HEAD")[:12], "dirty": bool(run("status", "--porcelain", "--", "carhunt", "scripts", "site"))}


def wait_ready(H: dict, deploy_id: str, minutes: int = 10) -> dict:
    for _ in range(minutes * 12):
        st = requests.get(f"{API}/deploys/{deploy_id}", headers=H, timeout=60).json()
        if st.get("state") in ("ready", "error"):
            return st
        time.sleep(5)
    sys.exit(f"deploy {deploy_id} still processing after {minutes} minutes — NOT published; "
             f"check the Netlify Deploys page")


def publish(H: dict, sid: str, deploy_id: str) -> None:
    """Make a ready deploy the live one (Netlify's restore endpoint)."""
    r = requests.post(f"{API}/sites/{sid}/deploys/{deploy_id}/restore", headers=H, timeout=120)
    r.raise_for_status()


def record(entry: dict) -> None:
    LAST.write_text(json.dumps(entry, indent=1), encoding="utf-8")
    with open(HISTORY, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({k: v for k, v in entry.items() if k not in ("files", "functions")})
                 + "\n")


def verify(base: str) -> bool:
    import subprocess
    r = subprocess.run([sys.executable, str(PROJECT_ROOT / "scripts" / "verify_deploy.py"),
                        "--base", base], cwd=PROJECT_ROOT)
    return r.returncode == 0


def rollback(target: str | None) -> None:
    """Re-publish an earlier deploy: the given id, or the one before the live one."""
    sid, H = site_id(), {"Authorization": f"Bearer {token()}"}
    if not target:
        try:
            lines = [json.loads(x) for x in HISTORY.read_text(encoding="utf-8").splitlines() if x.strip()]
        except OSError:
            lines = []
        live = json.loads(LAST.read_text(encoding="utf-8")).get("id") if LAST.exists() else None
        earlier = [e for e in lines if e.get("id") != live]
        if not earlier:
            sys.exit("no earlier deploy recorded in data/deploy_history.jsonl; pass an id")
        target = earlier[-1]["id"]
    publish(H, sid, target)
    print(f"rolled back: deploy {target} is live again")
    entry = {"id": target, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "rollback": True}
    try:
        prev = json.loads(LAST.read_text(encoding="utf-8"))
        entry["dossiers"] = next((json.loads(x).get("dossiers") for x in
                                  reversed(HISTORY.read_text(encoding="utf-8").splitlines())
                                  if x.strip() and json.loads(x).get("id") == target), None)
        entry["previous"] = prev.get("id")
    except (OSError, ValueError):
        pass
    record(entry)


def main() -> None:
    dry = "--dry-run" in sys.argv
    if "--rollback" in sys.argv:
        i = sys.argv.index("--rollback")
        rollback(sys.argv[i + 1] if len(sys.argv) > i + 1 and not sys.argv[i + 1].startswith("-") else None)
        return
    files = stage()
    funcs = stage_functions()
    dossiers = dossier_count(files)
    check_shrink(dossiers, "--allow-shrink" in sys.argv)
    print("staging:")
    for path, (sha, body) in files.items():
        print(f"  {path:<16} {len(body):>10,} bytes  sha1 {sha[:12]}")
    for name, (sha, body) in funcs.items():
        print(f"  fn:{name:<13} {len(body):>10,} bytes  sha256 {sha[:12]}")
    if dry:
        print("\n--dry-run: nothing sent")
        return

    # The payloads go to the free static host FIRST: the page about to be
    # published asks for them there by content hash, so they must exist before
    # it does. Not configured (exit 2) = same-origin only, still correct.
    import cloudflare_deploy
    cf = cloudflare_deploy.main()
    if cf == 1:
        sys.exit("REFUSED: the payload upload to Cloudflare failed — nothing published")

    sid, H = site_id(), {"Authorization": f"Bearer {token()}"}
    # A DRAFT first: it gets its own URL and is checked there before anyone
    # sees it. Until 2026-11-04 deploys went straight to production and were
    # only checked once live.
    r = requests.post(f"{API}/sites/{sid}/deploys", headers=H, timeout=120,
                      json={"files": {p: s for p, (s, _) in files.items()},
                            "functions": {n: s for n, (s, _) in funcs.items()},
                            "draft": True})
    r.raise_for_status()
    dep = r.json()
    deploy_id, required = dep["id"], dep.get("required") or []
    required_fns = dep.get("required_functions") or []
    print(f"\ndraft deploy {deploy_id} created")
    print(f"netlify needs {len(required)} file(s) and {len(required_fns)} function(s) uploaded")

    by_sha = {s: (p, b) for p, (s, b) in files.items()}
    for sha in required:
        path, body = by_sha[sha]
        up = requests.put(f"{API}/deploys/{deploy_id}/files{path}",
                          headers={**H, "Content-Type": "application/octet-stream"},
                          data=body, timeout=600)
        up.raise_for_status()
        print(f"  uploaded {path} ({len(body):,} bytes)")

    fn_by_sha = {s: (n, b) for n, (s, b) in funcs.items()}
    for sha in required_fns:
        name, body = fn_by_sha[sha]
        up = requests.put(f"{API}/deploys/{deploy_id}/functions/{name}",
                          params={"runtime": "js"},
                          headers={**H, "Content-Type": "application/octet-stream"},
                          data=body, timeout=600)
        up.raise_for_status()
        print(f"  uploaded function {name} ({len(body):,} bytes)")

    st = wait_ready(H, deploy_id)
    if st.get("state") == "error":
        sys.exit(f"draft failed: {st.get('error_message')} — nothing published")
    draft_url = st.get("deploy_ssl_url") or st.get("deploy_url")
    print(f"draft ready: {draft_url}\nchecking the draft before publishing...")
    if not verify(draft_url):
        sys.exit(f"REFUSED: the draft failed verification — nothing published. Inspect {draft_url}")

    try:
        previous = json.loads(LAST.read_text(encoding="utf-8")).get("id")
    except (OSError, ValueError):
        previous = None
    publish(H, sid, deploy_id)
    print(f"published: {deploy_id} is live (previous: {previous}; undo with --rollback)")
    record({"id": deploy_id, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "previous": previous,
            "dossiers": dossiers, **git_state(),
            "files": {p: s for p, (s, _) in files.items()},
            "functions": {n: s for n, (s, _) in funcs.items()}})
    if not verify("https://mycarsd.online"):
        print("WARNING: the LIVE site failed verification after publishing; "
              "consider: python scripts\\netlify_deploy.py --rollback")
        sys.exit(1)
    try:
        import indexnow
        indexnow.submit()
    except Exception as e:  # a ping failure must never fail a deploy
        print(f"indexnow: skipped ({e})")


if __name__ == "__main__":
    main()
