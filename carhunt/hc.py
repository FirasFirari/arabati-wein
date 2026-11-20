"""Manage the Healthchecks.io checks, so the owner does not have to.

deadman.py SENDS pings. This module manages the checks those pings land on,
through the Management API (config.yaml `healthchecks.api_key`, a read-write
project key; private).

Why (2026-11-16): every check notified email + Telegram, and a deliberate Pause
turned all of them DOWN, so the owner was messaged for days about a state they
had chosen. The owner's rule now:

  * Healthchecks tells a human about ONE thing: the whole PC unreachable for
    48 hours while NOT paused (`carhunt-alive`, Telegram only). That is the one
    failure nothing on this PC can report or repair.
  * Every other check is a silent dashboard. The watchdog repairs what it can
    (restart, catch-up) and speaks only when it has given up.
  * Pause silences the checks (a paused check never alerts and wakes on its
    next ping, so Resume needs no call here).

    python -m carhunt.hc status          # what Healthchecks currently thinks
    python -m carhunt.hc policy          # (re)apply the rule above; idempotent
    python -m carhunt.hc pause [--light] # called by scripts\\carhunt_control.ps1

Like deadman, nothing here may break the caller: failures print and return.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from . import deadman

API = "https://healthchecks.io/api/v3"
LAST_RESORT = "carhunt-alive"
LAST_RESORT_GRACE = 48 * 3600
LAST_RESORT_KIND = "telegram"
# stopped by a LIGHT pause (collector + scoring); everything else keeps running
LIGHT_SLUGS = {"daily-lane", "gemini-scoring", "photo-ocr"}
# (period, grace) in seconds for a check this module has to create
DEFAULTS = {"search-console": (86400, 12 * 3600)}


def _api_key() -> str:
    try:
        from .config import load_config
        return str(((load_config() or {}).get("healthchecks") or {}).get("api_key") or "")
    except Exception:
        return ""


def call(method: str, path: str, body: dict | None = None, key: str | None = None):
    key = _api_key() if key is None else key
    if not key:
        raise RuntimeError("healthchecks.api_key is not set in config.yaml")
    data = json.dumps(body).encode("utf-8") if body is not None else (b"" if method == "POST" else None)
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "X-Api-Key": key, "Content-Type": "application/json", "User-Agent": "carhunt-hc"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def checks(api=call) -> list[dict]:
    return api("GET", "/checks/")["checks"]


def plan(existing: list[dict], channels: list[dict]) -> list[tuple[str, str, dict]]:
    """The (method, path, body) calls that bring the project to the owner's rule."""
    last_resort = ",".join(c["id"] for c in channels if c["kind"] == LAST_RESORT_KIND)
    out = []
    have = set()
    for c in existing:
        slug = c.get("slug") or ""
        have.add(slug)
        want = {"channels": last_resort} if slug == LAST_RESORT else {"channels": ""}
        if slug == LAST_RESORT:
            want["grace"] = LAST_RESORT_GRACE
        now = {"channels": ",".join(sorted(filter(None, (c.get("channels") or "").split(",")))),
               "grace": c.get("grace")}
        want_cmp = dict(want, channels=",".join(sorted(filter(None, want["channels"].split(",")))))
        if any(now.get(k) != v for k, v in want_cmp.items()):
            out.append(("POST", f"/checks/{c['uuid']}", want))
    for slug in deadman.CHECKS:
        if slug not in have and slug in DEFAULTS:
            period, grace = DEFAULTS[slug]
            out.append(("POST", "/checks/", {"name": slug, "slug": slug, "tags": "carhunt",
                                             "timeout": period, "grace": grace,
                                             "channels": "", "unique": ["name"]}))
    return out


def apply_policy(api=call) -> int:
    calls = plan(checks(api), api("GET", "/channels/")["channels"])
    for method, path, body in calls:
        api(method, path, body)
    return len(calls)


def pause(light: bool = False, api=call) -> list[str]:
    """Silence the checks a pause is about to turn DOWN. Returns their slugs."""
    done = []
    for c in checks(api):
        slug = c.get("slug") or c.get("name") or ""
        if light and slug not in LIGHT_SLUGS:
            continue
        if c.get("status") in ("paused", "new"):
            continue
        api("POST", f"/checks/{c['uuid']}/pause")
        done.append(slug)
    return done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("status", "policy", "pause"))
    ap.add_argument("--light", action="store_true")
    args = ap.parse_args()
    try:
        if args.action == "policy":
            print(f"policy applied: {apply_policy()} change(s)")
        elif args.action == "pause":
            print("silenced: " + (", ".join(pause(args.light)) or "nothing to silence"))
        for c in sorted(checks(), key=lambda c: c.get("slug") or ""):
            n = len([x for x in (c.get("channels") or "").split(",") if x])
            print(f"  {c.get('slug') or c.get('name'):16} {c['status']:7} "
                  f"grace {c['grace'] / 3600:5.1f} h  notifies {n} channel(s)  "
                  f"last ping {(c.get('last_ping') or 'never')[:16]}")
    except (RuntimeError, urllib.error.URLError, OSError, KeyError, ValueError) as e:
        print(f"healthchecks: skipped ({type(e).__name__}: {e})")
        sys.exit(0)   # monitoring trouble must never fail a pause


if __name__ == "__main__":
    main()
