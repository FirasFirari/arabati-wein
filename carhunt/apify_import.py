"""Deep Facebook group backfill via Apify's official Facebook Groups Scraper
(public groups, no login/cookies, ~$2.60 per 1000 posts).

Setup: create an account at https://console.apify.com, copy the API token
(Settings > API & Integrations) and either set APIFY_TOKEN in the environment
or add `apify: {token: "..."}` to config.yaml.

Usage:
    python -m carhunt.apify_import 993842458658985 --limit 500   # one group
    python -m carhunt.apify_import all --limit 500               # every configured group
    python -m carhunt.ingest reanalyze                           # then score/extract

Posts are stored with --no-analyze semantics (fast, cheap); run reanalyze
afterwards to score them with the tiered analyzer.
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request

from . import db
from .config import load_config
from .ingest import add_one

ACTOR = "apify~facebook-groups-scraper"
API = "https://api.apify.com/v2"


def _token(cfg: dict) -> str:
    token = os.environ.get("APIFY_TOKEN") or (cfg.get("apify") or {}).get("token")
    if not token:
        sys.exit("No Apify token: set APIFY_TOKEN or apify.token in config.yaml")
    return token


def _api(method: str, path: str, token: str, body: dict | None = None) -> dict | list:
    url = f"{API}{path}{'&' if '?' in path else '?'}token={urllib.parse.quote(token)}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def scrape_group(token: str, group_id: str, limit: int) -> list[dict]:
    print(f"[{group_id}] starting Apify run (limit {limit})...")
    run = _api("POST", f"/acts/{ACTOR}/runs", token, {
        "startUrls": [{"url": f"https://www.facebook.com/groups/{group_id}"}],
        "resultsLimit": limit,
    })["data"]
    run_id = run["id"]
    while True:
        time.sleep(20)
        status = _api("GET", f"/actor-runs/{run_id}", token)["data"]
        print(f"[{group_id}] {status['status']}")
        if status["status"] in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            break
    if status["status"] != "SUCCEEDED":
        print(f"[{group_id}] run ended with {status['status']} — importing whatever it got")
    items = _api("GET", f"/datasets/{status['defaultDatasetId']}/items?format=json", token)
    print(f"[{group_id}] {len(items)} posts fetched")
    return items


def import_items(group_id: str, items: list[dict]) -> int:
    """Map Apify post items into the posts table (store-only; reanalyze later)."""
    added = 0
    for item in items:
        # built-in hash() is salted per process, so an item without an id would
        # get a different ref on every import and defeat deduplication
        post_id = (item.get("postId") or item.get("id")
                   or hashlib.sha256(
                       json.dumps(item, sort_keys=True, ensure_ascii=False)
                       .encode("utf-8")).hexdigest()[:24])
        ref = f"fb:{group_id}:{post_id}"
        if db.has_post(ref):
            continue
        url = item.get("url") or item.get("postUrl") or item.get("facebookUrl")
        text_parts = [item.get("text") or ""]
        user = item.get("user") or {}
        if isinstance(user, dict) and user.get("name"):
            text_parts.insert(0, f"الناشر: {user['name']}")
        if item.get("time"):
            text_parts.insert(0, f"تاريخ النشر: {item['time']}")
        images = []
        for att in item.get("attachments") or []:
            if not isinstance(att, dict):
                continue
            src = None
            for key in ("image", "photo_image"):
                val = att.get(key)
                if isinstance(val, dict) and val.get("uri"):
                    src = val["uri"]
                    break
            src = src or att.get("url")
            if src and str(src).startswith("http"):
                images.append(src)
        add_one(cfg={"matching": {"alert_threshold": 999}}, analyzer=None,
                source=f"facebook:{group_id}", ref=ref, url=url,
                text="\n".join(p for p in text_parts if p) or None,
                images=images[:6])
        added += 1
    return added


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("group", help="group id, comma list, 'all', or 'all-except:<id,id>'")
    parser.add_argument("--limit", type=int, default=500, help="posts per group")
    args = parser.parse_args()

    cfg = load_config()
    token = _token(cfg)
    db.init()
    all_ids = [g["id"] for g in cfg["facebook"]["groups"]]
    if args.group == "all":
        group_ids = all_ids
    elif args.group.startswith("all-except:"):
        skip = set(args.group.split(":", 1)[1].split(","))
        group_ids = [g for g in all_ids if g not in skip]
    else:
        group_ids = args.group.split(",")

    total = 0
    for gid in group_ids:
        try:
            items = scrape_group(token, gid, args.limit)
        except Exception as e:
            print(f"[{gid}] FAILED: {e}")
            continue
        added = import_items(gid, items)
        total += added
        print(f"[{gid}] {added} new posts stored")
    print(f"Done — {total} new posts. Now run: python -m carhunt.ingest reanalyze")


if __name__ == "__main__":
    main()
