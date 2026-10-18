"""Pull mycarsd.online's Clarity dashboard numbers through the Data Export API.

Token: Clarity > Settings > Data export > Generate new API token (project admins
only). Put it in `site/clarity_token.txt` (one line, gitignored alongside
clarity_id.txt) or in CLARITY_API_TOKEN. It is a JWT — treat it as a secret.

WHAT THIS CAN AND CANNOT DO — read before planning any analysis on it.

The API breaks the dashboard down by at most three of these dimensions:

    Browser · Device · Country/Region · OS · Source · Medium · Campaign
    · Channel · URL

**CUSTOM TAGS ARE NOT A DIMENSION.** The page sends the raw search text as a
Clarity custom tag (`clarity('set','search_query',…)` in export_site.py), and
this API cannot return it — not as a dimension, not as a metric. Verified
against the official reference 2026-09-01. The only way to read what people
actually typed is session recordings, one at a time, in the Clarity UI
(filter by the `search_zero_results` smart event). So this script answers
"how many and from where", never "what did they search for".

Core Web Vitals are likewise absent: no INP, LCP or CLS. Those stay in the
dashboard UI. What IS here: traffic, pages/session, scroll depth, engagement,
popular pages, referrers, and the frustration counters — dead clicks, rage
clicks, excessive scroll, quickbacks, script errors.

HARD QUOTAS, and they are small:
  * **10 requests per project per DAY.** Each dimension combination is one
    request, so plan the set before running; this script makes ONE call per
    invocation unless --dimensions is repeated.
  * Only the last 1-3 days (numOfDays=1|2|3). There is no history endpoint —
    to build a trend you must snapshot daily, which is what --save does.
  * 1,000 rows per response, no pagination.

Usage:
    python scripts\\clarity_export.py --days 3
    python scripts\\clarity_export.py --days 1 --dimensions Device Country/Region
    python scripts\\clarity_export.py --days 3 --save data\\clarity
"""
import argparse
import datetime
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# The report prints page titles and referrers, which on this project can carry
# Arabic. The Windows console is cp1252 and raises UnicodeEncodeError on the
# first one — the same crash that has killed three other scripts here.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import requests

ENDPOINT = "https://www.clarity.ms/export-data/api/v1/project-live-insights"
TOKEN_FILE = pathlib.Path("site/clarity_token.txt")

VALID_DIMENSIONS = {
    "Browser", "Device", "Country/Region", "OS",
    "Source", "Medium", "Campaign", "Channel", "URL",
}


def load_token() -> str:
    token = os.environ.get("CLARITY_API_TOKEN")
    if token:
        return token.strip()
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    sys.exit(
        f"No Clarity token. Put the JWT in {TOKEN_FILE} (one line) or set "
        f"CLARITY_API_TOKEN.\nGenerate it at Clarity > Settings > Data export "
        f"> Generate new API token."
    )


def fetch(token: str, days: int, dimensions: list[str]) -> list:
    params = {"numOfDays": str(days)}
    for i, dim in enumerate(dimensions[:3], start=1):
        params[f"dimension{i}"] = dim
    resp = requests.get(
        ENDPOINT, params=params, timeout=120,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
    )
    if resp.status_code == 429:
        sys.exit("429 — the 10-requests-per-day quota is spent. Try tomorrow "
                 "(the window is UTC).")
    if resp.status_code == 401:
        sys.exit("401 — token missing, invalid or expired. Regenerate it in "
                 "Clarity > Settings > Data export.")
    resp.raise_for_status()
    return resp.json()


def report(payload: list) -> None:
    for block in payload:
        name = block.get("metricName", "?")
        rows = block.get("information") or []
        print(f"\n=== {name}  ({len(rows)} row(s))")
        for row in rows[:15]:
            parts = [f"{k}={v}" for k, v in row.items()]
            print("   " + "  ".join(parts))
        if len(rows) > 15:
            print(f"   … {len(rows) - 15} more row(s)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, choices=(1, 2, 3), default=3,
                    help="the API only serves the last 1-3 days")
    ap.add_argument("--dimensions", nargs="*", default=[],
                    help=f"up to 3 of: {', '.join(sorted(VALID_DIMENSIONS))}. "
                         f"Custom tags are NOT supported — see the module "
                         f"docstring.")
    ap.add_argument("--save", type=pathlib.Path, default=None, metavar="DIR",
                    help="also write the raw JSON to DIR/clarity-<date>.json. "
                         "The API keeps no history, so a daily snapshot is the "
                         "only way to build a trend.")
    args = ap.parse_args()

    bad = [d for d in args.dimensions if d not in VALID_DIMENSIONS]
    if bad:
        sys.exit(f"Not valid dimension(s): {bad}. "
                 f"Allowed: {', '.join(sorted(VALID_DIMENSIONS))}. "
                 f"Custom tags such as search_query CANNOT be exported.")
    if len(args.dimensions) > 3:
        sys.exit("At most three dimensions per request.")

    payload = fetch(load_token(), args.days, args.dimensions)
    dims = ", ".join(args.dimensions) if args.dimensions else "(none)"
    print(f"Clarity — last {args.days} day(s), dimensions: {dims}")
    report(payload)

    if args.save:
        args.save.mkdir(parents=True, exist_ok=True)
        stamp = datetime.date.today().isoformat()
        out = args.save / f"clarity-{stamp}.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
