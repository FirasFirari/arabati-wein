"""Audit what each source actually returns for the harvest effort spent.

Joins the harvester's own telemetry (data/fb_yield.json: runs and new posts
per target) against what those posts became in the database. The question is
not "how many posts" but "how many usable vehicle records per visit", since
harvest time is the scarce resource — a Facebook sweep is paced deliberately
to avoid tripping the account.

Usage:
    python scripts/harvester_audit.py [--min-runs 1]
"""
import argparse
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from carhunt import db
from carhunt.config import load_config

YIELD_FILE = pathlib.Path("data/fb_yield.json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-runs", type=int, default=0)
    a = ap.parse_args()

    cfg = load_config()
    names = {g["id"]: g["name"] for g in cfg["facebook"]["groups"]}
    names.update({p["id"]: p["name"] for p in cfg["facebook"].get("profiles", [])})

    stats = json.loads(YIELD_FILE.read_text(encoding="utf-8")) if YIELD_FILE.exists() else {}
    db.init()
    conn = db.connect()
    now = datetime.datetime.now()

    rows = conn.execute("""
        SELECT p.source,
               COUNT(DISTINCT p.id)                                        AS posts,
               COUNT(DISTINCT CASE WHEN p.score IS NULL THEN p.id END)     AS queued,
               COUNT(DISTINCT v.post_id)                                   AS posts_with_veh,
               COUNT(v.id)                                                 AS vehicles,
               COUNT(DISTINCT CASE WHEN v.chassis_norm IS NOT NULL THEN v.id END) AS with_chassis,
               MAX(p.score)                                                AS top
        FROM posts p LEFT JOIN vehicles v ON v.post_id = p.id
        GROUP BY p.source""").fetchall()

    tiers = {}
    for r in conn.execute("SELECT source, verdict FROM posts WHERE verdict IS NOT NULL"):
        try:
            t = (json.loads(r["verdict"]) or {}).get("tier") or "?"
        except Exception:
            t = "?"
        d = tiers.setdefault(r["source"], {})
        d[t] = d.get(t, 0) + 1

    out = []
    for r in rows:
        src = r["source"]
        gid = src.split(":", 1)[1] if ":" in src else src
        ent = stats.get(gid, {})
        runs = ent.get("runs", 0)
        if runs < a.min_runs:
            continue
        t = tiers.get(src, {})
        dup = t.get("duplicate", 0)
        pre = t.get("prefiltered", 0) + t.get("prefiltered_offline", 0)
        hours = None
        if ent.get("last_run"):
            try:
                hours = (now - datetime.datetime.fromisoformat(ent["last_run"])).total_seconds() / 3600
            except ValueError:
                pass
        out.append({
            "src": src, "name": names.get(gid, ""), "runs": runs,
            "posts": r["posts"], "queued": r["queued"], "veh": r["vehicles"],
            "chassis": r["with_chassis"], "dup": dup, "pre": pre,
            "veh_per_run": (r["vehicles"] / runs) if runs else None,
            "veh_per_post": (r["vehicles"] / r["posts"]) if r["posts"] else 0,
            "dup_pct": 100 * dup / r["posts"] if r["posts"] else 0,
            "dead_pct": 100 * (dup + pre) / r["posts"] if r["posts"] else 0,
            "hours": hours, "top": r["top"],
        })

    fb = [o for o in out if o["src"].startswith("facebook")]
    tg = [o for o in out if o["src"].startswith("telegram")]

    def table(rows, title, key):
        print(f"\n=== {title} ===")
        print(f"{'source':<26}{'runs':>5}{'posts':>7}{'veh':>8}{'chassis':>8}"
              f"{'veh/run':>9}{'veh/post':>9}{'dead%':>7}{'hrs ago':>8}")
        for o in sorted(rows, key=key, reverse=True):
            vr = f"{o['veh_per_run']:.0f}" if o["veh_per_run"] is not None else "-"
            hr = f"{o['hours']:.1f}" if o["hours"] is not None else "-"
            print(f"{o['src'][:26]:<26}{o['runs']:>5}{o['posts']:>7}{o['veh']:>8}"
                  f"{o['chassis']:>8}{vr:>9}{o['veh_per_post']:>9.1f}"
                  f"{o['dead_pct']:>6.0f}%{hr:>8}")

    table(fb, "FACEBOOK — ranked by vehicles per harvest run", lambda o: o["veh_per_run"] or 0)
    table(tg, "TELEGRAM", lambda o: o["veh"])

    print("\n=== WHERE THE HARVEST EFFORT IS WASTED ===")
    waste = [o for o in fb if o["runs"] and o["dead_pct"] > 50 and o["posts"] >= 20]
    for o in sorted(waste, key=lambda o: -o["dead_pct"])[:12]:
        print(f"  {o['dead_pct']:>3.0f}% dead ({o['dup']} dup + {o['pre']} prefiltered) "
              f"of {o['posts']:>4} posts  {o['src']}")
    if not waste:
        print("  none above 50%")

    print("\n=== NEVER VISITED (no harvest run recorded) ===")
    configured = set(names)
    visited = set(stats)
    never = sorted(configured - visited)
    for gid in never:
        print(f"  {gid}  {names.get(gid,'')[:60]}")
    if not never:
        print("  none")

    tot_v = sum(o["veh"] for o in out)
    tot_p = sum(o["posts"] for o in out)
    print(f"\nTOTAL {tot_p} posts -> {tot_v} vehicles ({tot_v/tot_p:.1f} per post)")


if __name__ == "__main__":
    main()
