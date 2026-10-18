"""Pull the standing-watch form submissions from Netlify into data/watches.json.

The watch form on mycarsd.online posts to Netlify Forms. Netlify emails you on
each submission, but the DATA only ever lives in Netlify — nothing reaches this
project until something fetches it. This is that something.

TOKEN: Netlify > User settings > Applications > Personal access tokens > New
access token. Put it in `site/netlify_token.txt` (one line, gitignored) or set
NETLIFY_AUTH_TOKEN. It grants full account access — treat it as a secret.

    python scripts\\fetch_watches.py            # write data/watches.json
    python scripts\\fetch_watches.py --check    # then match them and report

Without a token you can still do this by hand: export the CSV from
https://app.netlify.com/projects/mycarsd/forms and save it as
`data/watches.csv`. Everything downstream reads either file.

SUBMISSIONS CARRY PHONE NUMBERS. The JSON this writes is personal data; `data/`
is gitignored, keep it that way and never paste its contents anywhere public.
"""
import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import requests

from carhunt import db, watches as watchlib
from carhunt.config import load_config

API = "https://api.netlify.com/api/v1"
SITE_ID = ((load_config().get("netlify") or {}).get("site_id")
           or os.environ.get("NETLIFY_SITE_ID") or "")   # Netlify project "mycarsd"
FORM_NAME = "watch"
TOKEN_FILE = next(iter(sorted(pathlib.Path("site").glob("[Nn]etlify_token.txt"))),
                  pathlib.Path("site/netlify_token.txt"))


def load_token() -> str:
    token = os.environ.get("NETLIFY_AUTH_TOKEN")
    if token:
        return token.strip()
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    sys.exit(
        f"No Netlify token. Put a personal access token in {TOKEN_FILE} (one "
        f"line) or set NETLIFY_AUTH_TOKEN.\nCreate one at Netlify > User "
        f"settings > Applications > Personal access tokens.\n\nOr skip the API "
        f"entirely: export the CSV from\n"
        f"https://app.netlify.com/projects/mycarsd/forms and save it as "
        f"data/watches.csv.")


def get(token: str, path: str, **params):
    resp = requests.get(f"{API}{path}", timeout=60, params=params,
                        headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 401:
        sys.exit("401 — the Netlify token is missing, invalid or revoked.")
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="after fetching, match the watches against the "
                         "registry and print who to contact")
    args = ap.parse_args()

    token = load_token()
    if not SITE_ID:
        sys.exit("No Netlify site id: set netlify.site_id in config.yaml or NETLIFY_SITE_ID.")

    # Look the form up by NAME rather than hardcoding its id: deleting and
    # re-adding the form in Netlify mints a new id, and a stale hardcoded one
    # would silently fetch nothing.
    forms = get(token, f"/sites/{SITE_ID}/forms")
    form = next((f for f in forms if f.get("name") == FORM_NAME), None)
    if form is None:
        sys.exit(f"No form named {FORM_NAME!r} on the site. Found: "
                 f"{[f.get('name') for f in forms]}")

    # Netlify pages at 100; walk until a short page comes back.
    subs, page = [], 1
    while True:
        batch = get(token, f"/forms/{form['id']}/submissions",
                    per_page=100, page=page)
        subs.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    rows = []
    for s in subs:
        data = s.get("data") or {}
        rows.append({
            "identifier": (data.get("identifier") or "").strip(),
            "contact": (data.get("contact") or "").strip(),
            "note": (data.get("note") or "").strip(),
            "created_at": s.get("created_at") or "",
        })

    watchlib.WATCHES_JSON.parent.mkdir(parents=True, exist_ok=True)
    watchlib.WATCHES_JSON.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    usable = sum(1 for r in rows if r["identifier"])
    print(f"fetched {len(rows)} submission(s), {usable} with an identifier "
          f"-> {watchlib.WATCHES_JSON}")

    if args.check:
        db.init()
        conn = db.connect()
        try:
            for row in watchlib.status_rows(conn):
                mark = "MATCH" if row["hits"] else "     "   # unreviewed only
                print(f"  {mark}  {row['identifier']:<20} "
                      f"{row['status']:<14} {row['note']}")
        finally:
            conn.close()
        print("\nFull report with contact details: "
              "python scripts\\check_watches.py")


if __name__ == "__main__":
    main()
