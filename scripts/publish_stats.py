"""Public statistics: site/static/stats.json and stats.html from the database.

Read-only (`mode=ro`), so it can run beside the collector. No per-post queries.
The figures are the ones we publish and must stay honest: distinct chassis and
identified vehicles, never raw sighting rows; leads relayed and recoveries
confirmed come from the hand-kept data/impact.json because only a human knows.

    python scripts\\publish_stats.py            # write stats.json + stats.html
    python scripts\\publish_stats.py --print    # show the numbers only
"""
import datetime
import json
import pathlib
import re
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from carhunt import watches as watchlib
from carhunt.config import DATA_DIR, PROJECT_ROOT
from carhunt.db import DB_PATH, DISTINCT_VEHICLES_SQL

STATIC = PROJECT_ROOT / "site" / "static"
IMPACT = DATA_DIR / "impact.json"
TEMPLATE = STATIC / "stats.template.html"


def impact() -> dict:
    try:
        return json.loads(IMPACT.read_text(encoding="utf-8"))
    except Exception:
        return {"leads_relayed": 0, "recoveries_confirmed": 0, "note": "impact.json missing"}


def compute() -> dict:
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    q = lambda sql: conn.execute(sql).fetchone()[0]
    posts = q("SELECT COUNT(*) FROM posts")
    first, last = conn.execute("SELECT MIN(seen_at), MAX(seen_at) FROM posts").fetchone()
    by_platform = {}
    for src, n in conn.execute(
            "SELECT substr(source, 1, instr(source || ':', ':') - 1), COUNT(*) FROM posts GROUP BY 1"):
        by_platform[src or "other"] = n
    stats = {
        "schema": 1,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "posts_collected": posts,
        "posts_by_platform": by_platform,
        "first_post": (first or "")[:10],
        "latest_post": (last or "")[:10],
        "sighting_rows": q("SELECT COUNT(*) FROM vehicles"),
        "identified_vehicles": q(DISTINCT_VEHICLES_SQL),
        "distinct_chassis": q("SELECT COUNT(DISTINCT chassis_norm) FROM vehicles "
                              "WHERE chassis_norm IS NOT NULL AND chassis_norm <> ''"),
        "distinct_plates": q("SELECT COUNT(DISTINCT plate_norm) FROM vehicles "
                             "WHERE plate_norm IS NOT NULL AND plate_norm <> ''"),
        "photos_collected": q("SELECT COUNT(*) FROM media"),
        "photos_transcribed": q("SELECT COUNT(*) FROM media WHERE ocr_at IS NOT NULL "
                                "AND (ocr_error IS NULL OR ocr_error = '')"),
        "posts_unprocessed": q("SELECT COUNT(*) FROM posts WHERE score IS NULL"),
        "last_extraction": (q("SELECT MAX(extracted_at) FROM vehicles") or "")[:10],
    }
    conn.close()
    watches = [w for w in watchlib.load_watches() if "TEST" not in w["identifier"].upper()]
    stats["standing_watches"] = len(watches)
    imp = impact()
    stats["leads_relayed"] = int(imp.get("leads_relayed", 0))
    stats["recoveries_confirmed"] = int(imp.get("recoveries_confirmed", 0))
    stats["definitions"] = {
        "identified_vehicles": "distinct normalised chassis numbers, plus distinct plates on rows with no chassis",
        "sighting_rows": "raw extracted rows; the same list is reposted to many groups, so this overstates vehicles",
        "leads_relayed": "standing-watch hits a human verified and passed to the registrant",
        "recoveries_confirmed": "cars whose owner confirmed recovery to us after a registry lead",
    }
    return stats


def fmt(n) -> str:
    return f"{n:,}" if isinstance(n, int) else str(n)


def render_html(stats: dict) -> str:
    tpl = TEMPLATE.read_text(encoding="utf-8")
    rows = [
        ("Distinct chassis numbers searchable", "أرقام شاسي مميزة قابلة للبحث", stats["distinct_chassis"]),
        ("Identified vehicles (chassis or plate)", "عربات معرَّفة (شاسي أو لوحة)", stats["identified_vehicles"]),
        ("Distinct plates", "لوحات مميزة", stats["distinct_plates"]),
        ("Posts collected", "منشورات مجمَّعة", stats["posts_collected"]),
        ("Photos collected", "صور مجمَّعة", stats["photos_collected"]),
        ("Photos transcribed (OCR)", "صور مقروءة آلياً", stats["photos_transcribed"]),
        ("Posts awaiting processing", "منشورات في انتظار المعالجة", stats["posts_unprocessed"]),
        ("Standing watches", "أرقام مسجَّلة للمتابعة", stats["standing_watches"]),
        ("Leads relayed to owners", "تنبيهات سُلِّمت لأصحابها", stats["leads_relayed"]),
        ("Recoveries confirmed by owners", "استردادات مؤكَّدة من أصحابها", stats["recoveries_confirmed"]),
    ]
    table = "\n".join(
        f'<tr><td>{en}<span class="ar" lang="ar" dir="rtl">{ar}</span></td><td class="num">{fmt(v)}</td></tr>'
        for en, ar, v in rows)
    out = (tpl.replace("__STATS_TABLE__", table)
              .replace("__GENERATED__", stats["generated_at"][:10])
              .replace("__FIRST_POST__", stats["first_post"])
              .replace("__LATEST_POST__", stats["latest_post"])
              .replace("__LAST_EXTRACTION__", stats["last_extraction"])
              .replace("__SIGHTING_ROWS__", fmt(stats["sighting_rows"])))
    for k, v in stats.items():
        if isinstance(v, int):
            out = out.replace(f"__{k.upper()}__", fmt(v))
    return out


def main() -> None:
    stats = compute()
    if "--print" in sys.argv:
        print(json.dumps(stats, ensure_ascii=False, indent=1))
        return
    STATIC.mkdir(parents=True, exist_ok=True)
    (STATIC / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    if TEMPLATE.exists():
        (STATIC / "stats.html").write_text(render_html(stats), encoding="utf-8")
    print(f"stats.json written: {stats['distinct_chassis']:,} distinct chassis, "
          f"{stats['identified_vehicles']:,} identified vehicles, {stats['standing_watches']} watches")


if __name__ == "__main__":
    main()
