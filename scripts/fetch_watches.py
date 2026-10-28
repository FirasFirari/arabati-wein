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

NEW REGISTRATIONS -> TELEGRAM. Since 2026-10-28 the INSTANT alert comes from
Netlify itself: site/functions/submission-created.js runs the moment a
submission lands and messages the operator's chat, PC on or off. This script
therefore no longer announces by default (it would be a duplicate up to an
hour late). --notify turns the old per-fetch announcement back on - the
fallback if the function is ever broken (check: scripts/netlify_set_telegram_env.py
--show lists its secrets; the Netlify Functions log shows its runs). Either
way the message carries the identifier, the note and the contact TYPE only,
never the contact itself; the Netlify email and check_watches.py have the
number. The first fetch on a machine with no watches.json is a silent
baseline, so a wiped file cannot replay 60 alerts.

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


def _key(row: dict) -> tuple:
    return (row.get("created_at") or "", row.get("identifier") or "")


def load_previous():
    """Keys of the submissions already on disk, or None when there is no file.

    None (not an empty set) is what makes the very first fetch silent: with no
    baseline every row would otherwise count as new.
    """
    if not watchlib.WATCHES_JSON.exists():
        return None
    try:
        old = json.loads(watchlib.WATCHES_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return {_key(r) for r in old if isinstance(r, dict)}


def _contact_type(contact: str) -> str:
    from carhunt import notify
    if notify.is_email(contact):
        return "email"
    if notify.normalize_phone(contact):
        return "phone"
    return "unparsed" if contact else "none"


def announce_new(new_rows: list[dict], total: int) -> None:
    """One Telegram message for every registration not seen before. Never fatal.

    No contact details on purpose: the Netlify email already has them and
    `check_watches.py` prints them locally. Telegram is a third party.
    """
    try:
        from carhunt import notify
        lines = [f"CarHunt: {len(new_rows)} new standing-watch registration(s) "
                 f"({total} total)"]
        for r in sorted(new_rows, key=lambda r: r["created_at"]):
            when = (r["created_at"] or "")[:16].replace("T", " ")
            ident = r["identifier"] or "(no identifier)"
            note = (r["note"] or "").replace("\n", " ").strip()
            line = f"• {ident} · {when} UTC · contact: {_contact_type(r['contact'])}"
            if note:
                line += f"\n  note: {note[:160]}"
            lines.append(line)
        lines.append("The hourly sweep checks them against all posts; a hit is a "
                     "separate alert. Contacts: python scripts\\check_watches.py")
        ok = notify.send_telegram("\n".join(lines))
        notify.log(f"new registrations: {len(new_rows)} -> telegram {'sent' if ok else 'NOT sent'}")
        print("telegram sent" if ok else "telegram NOT sent — see data/logs/notify.log")
    except Exception as e:   # noqa: BLE001 — the fetch must still succeed
        print(f"announce failed: {e}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="after fetching, match the watches against the "
                         "registry and print who to contact")
    ap.add_argument("--notify", action="store_true",
                    help="announce new registrations on Telegram from here too "
                         "(normally the Netlify function already did, instantly)")
    ap.add_argument("--no-notify", action="store_true",
                    help="(kept for old callers; announcing is already off by default)")
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

    previous = load_previous()
    watchlib.WATCHES_JSON.parent.mkdir(parents=True, exist_ok=True)
    watchlib.WATCHES_JSON.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    usable = sum(1 for r in rows if r["identifier"])
    print(f"fetched {len(rows)} submission(s), {usable} with an identifier "
          f"-> {watchlib.WATCHES_JSON}")

    new_rows = [] if previous is None else [r for r in rows if _key(r) not in previous]
    if new_rows:
        print(f"{len(new_rows)} new registration(s) since the last fetch")
    if new_rows and args.notify and not args.no_notify:
        announce_new(new_rows, len(rows))

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
