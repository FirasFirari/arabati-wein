"""Monitor mycarsd.online in Google Search Console: snapshot, check, alert.

Access: the service account `gsc-reader@<car hunt project>` is a Full user on
the property `https://mycarsd.online/` (Search Console > Settings > Users and
permissions). Its JSON key lives in `site/gsc_key.txt` (gitignored by
`*key*.txt`) or at the path in GSC_KEY_FILE. The Search Console API is free
and needs no billing account. Treat the key as a secret.

What one run does (about 12 API calls; the quotas are 1,200 queries/minute
and 2,000 URL inspections/day, so there is nothing to ration, unlike Clarity):

  * search performance for the last 28 days by day, query, page, country and
    device. Unlike Clarity, Search Console keeps 16 months, so a missed day
    loses nothing; the snapshot is for convenience and for the trend check.
  * the submitted sitemap: errors, warnings, last time Google read it.
  * URL inspection of every URL in the LIVE sitemap: indexed or not, and why.

The last 2-3 days of performance data are always incomplete. Every trend
check below therefore ignores the most recent FRESH_DAYS days.

ALERTS go to the operator's Telegram chat and only when something CHANGES
(state in data/gsc/state.json), so a standing problem is reported once:

  * a sitemap URL's index status changed (either direction)
  * the sitemap has errors or warnings, or Google has not read it for 21 days
  * clicks in the last 7 complete days fell by half against the 7 before
    (only when the earlier week had at least 10 clicks)
  * impressions were zero on each of the last 3 complete days

Queries are what people typed into Google. They can contain a chassis or
plate number, so the snapshot stays in data/ (gitignored) and alerts never
quote a query.

Usage:
    python scripts\\gsc_export.py                 # report only
    python scripts\\gsc_export.py --save          # + data\\gsc\\gsc-<date>.json, checks, alerts
    python scripts\\gsc_export.py --save --quiet  # the scheduled task
    python scripts\\gsc_export.py --resubmit-sitemap   # after a deploy
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SITE = "https://mycarsd.online/"
SITEMAP = SITE + "sitemap.xml"
KEY_FILE = pathlib.Path(os.environ.get("GSC_KEY_FILE") or "site/gsc_key.txt")
OUT_DIR = pathlib.Path("data/gsc")
STATE_FILE = OUT_DIR / "state.json"
SCOPE = "https://www.googleapis.com/auth/webmasters"
API = "https://www.googleapis.com/webmasters/v3"
INSPECT = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"

WINDOW_DAYS = 28
FRESH_DAYS = 3          # newest days are incomplete; trend checks skip them
SITEMAP_STALE_DAYS = 21
MIN_CLICKS_FOR_DROP = 10


def session():
    if not KEY_FILE.exists():
        sys.exit(f"No Search Console key at {KEY_FILE}. See the module docstring.")
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    creds = service_account.Credentials.from_service_account_file(
        str(KEY_FILE), scopes=[SCOPE])
    return AuthorizedSession(creds)


def _site() -> str:
    return urllib.parse.quote(SITE, safe="")


def query(s, start: str, end: str, dimensions: list[str], limit: int = 250) -> list:
    r = s.post(f"{API}/sites/{_site()}/searchAnalytics/query", timeout=60, json={
        "startDate": start, "endDate": end, "dimensions": dimensions,
        "rowLimit": limit, "dataState": "all"})
    r.raise_for_status()
    return [{"keys": row["keys"], "clicks": row["clicks"],
             "impressions": row["impressions"], "ctr": round(row["ctr"], 4),
             "position": round(row["position"], 1)}
            for row in r.json().get("rows", [])]


def sitemaps(s) -> list:
    r = s.get(f"{API}/sites/{_site()}/sitemaps", timeout=60)
    r.raise_for_status()
    return r.json().get("sitemap", [])


def sitemap_urls() -> list[str]:
    """The URLs in the LIVE sitemap: what Google is actually told to index."""
    import requests
    r = requests.get(SITEMAP, timeout=60)
    r.raise_for_status()
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)


def inspect(s, url: str) -> dict:
    r = s.post(INSPECT, timeout=120, json={"inspectionUrl": url, "siteUrl": SITE})
    if r.status_code != 200:
        return {"url": url, "verdict": "ERROR", "coverage": f"HTTP {r.status_code}"}
    idx = (r.json().get("inspectionResult") or {}).get("indexStatusResult") or {}
    return {
        "url": url,
        "verdict": idx.get("verdict", "?"),            # PASS = indexed
        "coverage": idx.get("coverageState", "?"),
        "last_crawl": idx.get("lastCrawlTime", ""),
        "google_canonical": idx.get("googleCanonical", ""),
        "fetch": idx.get("pageFetchState", ""),
        "robots": idx.get("robotsTxtState", ""),
    }


def resubmit_sitemap(s) -> None:
    feed = urllib.parse.quote(SITEMAP, safe="")
    r = s.put(f"{API}/sites/{_site()}/sitemaps/{feed}", timeout=60)
    r.raise_for_status()
    print(f"sitemap resubmitted: {SITEMAP}")


def collect(s) -> dict:
    today = datetime.date.today()
    start = (today - datetime.timedelta(days=WINDOW_DAYS)).isoformat()
    end = today.isoformat()
    return {
        "pulled_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "site": SITE, "start": start, "end": end,
        "by_date": query(s, start, end, ["date"]),
        "by_query": query(s, start, end, ["query"]),
        "by_page": query(s, start, end, ["page"]),
        "by_country": query(s, start, end, ["country"]),
        "by_device": query(s, start, end, ["device"]),
        "sitemaps": sitemaps(s),
        "pages": [inspect(s, u) for u in sitemap_urls()],
    }


# ------------------------------------------------------------------ checks --
def complete_days(snap: dict) -> dict[str, dict]:
    """date -> row, for days old enough to be final, zero-filled."""
    rows = {r["keys"][0]: r for r in snap["by_date"]}
    last = datetime.date.fromisoformat(snap["end"]) - datetime.timedelta(days=FRESH_DAYS)
    out = {}
    for i in range(14):
        d = (last - datetime.timedelta(days=i)).isoformat()
        out[d] = rows.get(d, {"clicks": 0, "impressions": 0})
    return out


def problems(snap: dict) -> dict[str, str]:
    """Stable key -> human line, for every condition that is wrong right now."""
    out = {}
    for p in snap["pages"]:
        if p["verdict"] != "PASS":
            out[f"page:{p['url']}"] = f"not indexed: {p['url']} ({p['coverage']})"
    maps = snap["sitemaps"]
    if not maps:
        out["sitemap:missing"] = "no sitemap is submitted"
    for m in maps:
        if int(m.get("errors") or 0) or int(m.get("warnings") or 0):
            out["sitemap:errors"] = (f"sitemap has {m.get('errors')} error(s), "
                                     f"{m.get('warnings')} warning(s)")
        read = (m.get("lastDownloaded") or "")[:10]
        if read:
            age = (datetime.date.today() - datetime.date.fromisoformat(read)).days
            if age > SITEMAP_STALE_DAYS:
                out["sitemap:stale"] = f"Google last read the sitemap {age} days ago"
    days = complete_days(snap)
    order = sorted(days, reverse=True)
    recent = sum(days[d]["clicks"] for d in order[:7])
    before = sum(days[d]["clicks"] for d in order[7:14])
    if before >= MIN_CLICKS_FOR_DROP and recent * 2 <= before:
        out["clicks:drop"] = f"Google clicks fell from {before:g} to {recent:g} week on week"
    if all(days[d]["impressions"] == 0 for d in order[:3]):
        out["impressions:zero"] = f"no Google impressions on {order[2]} .. {order[0]}"
    return out


def compare(now: dict[str, str], state_file: pathlib.Path = STATE_FILE) -> tuple[list, list]:
    """(new problems, resolved problems) against the last run; updates state."""
    try:
        old = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        old = None
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(now, ensure_ascii=False, indent=2), encoding="utf-8")
    if old is None:          # first run: a baseline, not a flood of alerts
        return [], []
    new = [v for k, v in now.items() if k not in old]
    gone = [v for k, v in old.items() if k not in now]
    return new, gone


# ------------------------------------------------------------------ report --
def report(snap: dict, probs: dict[str, str]) -> None:
    days = complete_days(snap)
    order = sorted(days, reverse=True)
    wk = lambda ds, f: sum(days[d][f] for d in ds)
    total_c = sum(r["clicks"] for r in snap["by_date"])
    total_i = sum(r["impressions"] for r in snap["by_date"])
    print(f"Search Console, {snap['start']} .. {snap['end']} "
          f"(last {FRESH_DAYS} days incomplete)")
    print(f"  28 days: {total_c:g} clicks, {total_i:g} impressions")
    print(f"  last 7 complete days ({order[6]} .. {order[0]}): "
          f"{wk(order[:7], 'clicks'):g} clicks, {wk(order[:7], 'impressions'):g} impressions")
    print(f"  7 days before:                                   "
          f"{wk(order[7:14], 'clicks'):g} clicks, {wk(order[7:14], 'impressions'):g} impressions")
    print("\n  day         clicks  impressions  position")
    for r in snap["by_date"][-14:]:
        print(f"  {r['keys'][0]}  {r['clicks']:6g}  {r['impressions']:11g}  {r['position']:8}")
    for name, key, n in (("queries", "by_query", 15), ("pages", "by_page", 10),
                         ("countries", "by_country", 8), ("devices", "by_device", 3)):
        print(f"\n  top {name} (clicks, impressions, position)")
        rows = sorted(snap[key], key=lambda r: (-r["clicks"], -r["impressions"]))
        for r in rows[:n]:
            print(f"   {r['clicks']:4g} {r['impressions']:5g} {r['position']:5}  {r['keys'][0]}")
    print("\n  sitemap")
    for m in snap["sitemaps"]:
        sub = sum(int(c.get("submitted") or 0) for c in m.get("contents") or [])
        print(f"   {m['path']}: {sub} URLs, errors {m.get('errors')}, warnings "
              f"{m.get('warnings')}, last read {(m.get('lastDownloaded') or '?')[:10]}")
    print("\n  index status of each sitemap URL")
    for p in snap["pages"]:
        mark = "indexed    " if p["verdict"] == "PASS" else "NOT INDEXED"
        print(f"   {mark}  {p['url']}  [{p['coverage']}; crawled "
              f"{(p['last_crawl'] or 'never')[:10]}]")
    print("\n  problems now: " + ("none" if not probs else ""))
    for line in probs.values():
        print(f"   - {line}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true",
                    help=f"write {OUT_DIR}\\gsc-<date>.json, compare with the last "
                         f"run and send a Telegram alert on any change")
    ap.add_argument("--quiet", action="store_true", help="no report on stdout")
    ap.add_argument("--no-alert", action="store_true",
                    help="with --save: update the state but send nothing")
    ap.add_argument("--resubmit-sitemap", action="store_true",
                    help="tell Google the sitemap changed, then exit")
    args = ap.parse_args()

    s = session()
    if args.resubmit_sitemap:
        resubmit_sitemap(s)
        return

    snap = collect(s)
    probs = problems(snap)
    if not args.quiet:
        report(snap, probs)
    if args.save:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / f"gsc-{datetime.date.today().isoformat()}.json"
        out.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
        new, gone = compare(probs)
        if not args.quiet:
            print(f"\nsaved {out}; {len(new)} new problem(s), {len(gone)} resolved")
        if (new or gone) and not args.no_alert:
            from carhunt import notify
            lines = ["Search Console (mycarsd.online)"]
            lines += [f"NEW: {x}" for x in new]
            lines += [f"resolved: {x}" for x in gone]
            notify.send_telegram("\n".join(lines))


if __name__ == "__main__":
    from carhunt import deadman
    with deadman.tracked("gsc", "search-console"):
        main()
