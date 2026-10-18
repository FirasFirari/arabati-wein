"""Continuous Facebook harvesting.

Cycles through the configured groups AND profiles forever, harvesting a few
per pass so each cycle is short and the account is never hammered. Between
cycles it rests. Emits a heartbeat so the watchdog can tell working from
merely alive.

Facebook has no API and a permanently-open session is both fragile and
riskier for the account, so "continuous" here means a paced rotation rather
than a persistent listener.

The rotation is YIELD-WEIGHTED rather than round-robin. Measured over a day,
the targets differ by ~5.5x in how much they actually produce: one group
returned 48 new posts per visit while another returned 8.75 from four visits
(268 posts seen, nearly all already stored). Round-robin spends the same
scarce, deliberately-paced budget on both. Priority is

    (average new posts per visit + 1) x hours since last visited

so productive targets come round sooner, while the multiplier on elapsed
time means a quiet group's priority climbs until it is picked anyway — no
target can be starved, which matters when any one of them could be the one
carrying the car. Never-visited targets go first.

Usage:
    python -m carhunt.fb_service
    python -m carhunt.fb_service --per-cycle 4 --rest 900 --scrolls 30
    python -m carhunt.fb_service --round-robin      # the old fixed order
"""
import argparse
import asyncio
import datetime
import json

from . import db, health
from .config import DATA_DIR, load_config
from .fb_harvest import (DEFAULT_QUERIES, PROFILE_DIR, configured_groups,
                         configured_profiles, harvest_feed, harvest_group,
                         harvest_profile)

LOG = DATA_DIR / "logs" / "fb_service.log"
# per-target harvest history, so the weighting survives the frequent restarts
YIELD_FILE = DATA_DIR / "fb_yield.json"


def load_yield() -> dict:
    try:
        return json.loads(YIELD_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}       # first run, or the file was corrupted — start over


def save_yield(stats: dict) -> None:
    try:
        YIELD_FILE.write_text(json.dumps(stats, indent=1), encoding="utf-8")
    except OSError as e:
        log(f"could not save yield stats: {e}")


def priority(stats: dict, target_id: str, now: datetime.datetime) -> float:
    """How much this target has earned the next slot."""
    entry = stats.get(target_id)
    if not entry or not entry.get("runs"):
        return float("inf")             # never visited: look at it first
    average_new = entry["new"] / entry["runs"]
    try:
        last = datetime.datetime.fromisoformat(entry["last_run"])
    except (KeyError, ValueError):
        return float("inf")
    hours = max((now - last).total_seconds() / 3600.0, 0.01)
    # +1 keeps a target that has yielded nothing in the running, on elapsed
    # time alone; without it a single empty visit would bench it forever
    return (average_new + 1.0) * hours


def record_yield(stats: dict, target_id: str, new_posts: int,
                 now: datetime.datetime) -> None:
    entry = stats.setdefault(target_id, {"runs": 0, "new": 0})
    entry["runs"] += 1
    entry["new"] += new_posts
    entry["last_new"] = new_posts
    entry["last_run"] = now.isoformat()


def log(message: str) -> None:
    # print only: the launcher .bat redirects stdout to the log file, and
    # opening the same file here would fight it for the Windows lock
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


async def run(per_cycle: int, rest: int, scrolls: int, pause: float,
              mode: str, round_robin: bool = False) -> None:
    from playwright.async_api import async_playwright

    cfg = load_config()
    group_ids = configured_groups(cfg)
    profile_ids = configured_profiles(cfg)
    # sources opted out of photo download (dealer groups — see config.yaml)
    no_media = {g["id"] for g in (cfg.get("facebook") or {}).get("groups", [])
                if g.get("media") is False}
    if no_media:
        log(f"photo capture disabled for {len(no_media)} source(s): "
            + ", ".join(sorted(no_media)))
    # One rotation over both kinds, so profiles get the same paced treatment
    # and nothing has to be scheduled separately.
    targets = ([("profile", pid) for pid in profile_ids]
               + [("group", gid) for gid in group_ids])
    if not targets:
        log("no groups or profiles configured")
        return
    stats = load_yield()
    order = "round-robin" if round_robin else "yield-weighted"
    log(f"starting: {len(group_ids)} groups + {len(profile_ids)} profiles, "
        f"{per_cycle} per cycle, {rest}s rest, mode={mode}, order={order}, "
        f"history for {len(stats)} target(s)")

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.facebook.com/",
                        wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)
        if "login" in page.url or "checkpoint" in page.url:
            log("NOT LOGGED IN — log into Facebook in the open window, "
                "then restart this service")
            await page.wait_for_timeout(180000)
            await context.close()
            return

        db.init()

        # One continuous heartbeat for the whole service. Harvesting a single
        # group can take minutes and the rest between cycles is 15, so beating
        # only at cycle boundaries makes a healthy service look dead.
        status = {"text": "starting"}

        async def heartbeat():
            while True:
                health.beat("fb_service", status["text"])
                await asyncio.sleep(30)

        asyncio.create_task(heartbeat())

        cursor = 0
        while True:
            size = min(per_cycle, len(targets))
            if round_robin:
                batch = [targets[(cursor + i) % len(targets)] for i in range(size)]
                cursor = (cursor + size) % len(targets)
            else:
                now = datetime.datetime.now()
                batch = sorted(
                    targets, key=lambda t: priority(stats, t[1], now),
                    reverse=True)[:size]

            total = 0
            for kind, tid in batch:
                status["text"] = f"harvesting {kind} {tid}"
                gained = 0
                try:
                    if kind == "profile":
                        # profiles have no in-page search: timeline only
                        gained = await harvest_profile(page, tid, scrolls, pause)
                    else:
                        want_media = tid not in no_media
                        if mode in ("feed", "both"):
                            gained += await harvest_feed(page, tid, scrolls,
                                                         pause, want_media)
                        if mode in ("search", "both"):
                            gained += await harvest_group(
                                page, tid, DEFAULT_QUERIES,
                                max(6, scrolls // 4), pause, want_media)
                except Exception as e:
                    log(f"[{tid}] failed: {str(e)[:180]}")
                    # a failed visit still counts as a visit: without this its
                    # priority stays infinite and the cycle retries it forever
                total += gained
                record_yield(stats, tid, gained, datetime.datetime.now())
                await page.wait_for_timeout(int(pause * 2000))
            save_yield(stats)

            ranked = ", ".join(
                f"{tid}+{stats[tid]['last_new']}"
                f"(avg {stats[tid]['new'] / stats[tid]['runs']:.1f})"
                for _, tid in batch if tid in stats)
            log(f"cycle done: {ranked} -> {total} new posts; resting {rest}s")
            for remaining in range(rest, 0, -30):
                status["text"] = (f"resting {round(remaining / 60)}m "
                                  f"(last cycle +{total})")
                await asyncio.sleep(min(30, remaining))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-cycle", type=int, default=3,
                        help="groups harvested per cycle")
    parser.add_argument("--rest", type=int, default=900,
                        help="seconds to rest between cycles")
    parser.add_argument("--scrolls", type=int, default=30)
    parser.add_argument("--pause", type=float, default=2.5)
    parser.add_argument("--mode", choices=["feed", "search", "both"],
                        default="feed")
    parser.add_argument("--round-robin", action="store_true",
                        help="visit targets in config order instead of by yield")
    args = parser.parse_args()
    asyncio.run(run(args.per_cycle, args.rest, args.scrolls, args.pause,
                    args.mode, args.round_robin))


if __name__ == "__main__":
    main()
