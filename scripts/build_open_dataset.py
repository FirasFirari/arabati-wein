"""Build the open aggregate dataset in open-data/ from the database.

Counts only. No chassis, plate, contact, post text, photo, link or watch
registration leaves the database through this script: every output row is a
count of distinct vehicles or posts in a category, and a category holding
fewer than SUPPRESS_BELOW vehicles is published without its number.

Each CSV carries an HXL hashtag row under the header, and state rows carry the
OCHA P-code, so the files join to the Sudan administrative boundaries
(https://data.humdata.org/dataset/cod-ab-sdn) without a lookup table.

Read-only (`mode=ro`): safe beside the collector.

    python scripts\\build_open_dataset.py            # write open-data/*.csv
    python scripts\\build_open_dataset.py --print    # show the tables only
"""
import collections
import csv
import datetime
import io
import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from carhunt import geo
from carhunt.config import PROJECT_ROOT
from carhunt.db import DB_PATH

OUT = PROJECT_ROOT / "open-data"
SUPPRESS_BELOW = 5          # a cell of 1-4 is published as "1-4", not as its number
MIN_MAKE = 50               # makes below this are grouped as "Other makes"

# A vehicle is a distinct normalised chassis; a row with no chassis counts by
# its plate. Rows with neither cannot be told apart and are not counted.
VEHICLE_KEY = "COALESCE(NULLIF(chassis_norm, ''), 'p:' || NULLIF(plate_norm, ''))"

STATUS = {
    "found": "found", "recovered": "recovered", "seeking_owner": "seeking_owner",
    "spotted": "spotted", "for_sale": "for_sale", "unknown": "not_stated",
    "stolen": "reported_missing", "lost_stolen": "reported_missing",
    "lost": "reported_missing", "missing": "reported_missing",
    "in_custody": "in_custody", "seized": "in_custody", "seized_in_operation": "in_custody",
}
STATUS_MEANING = {
    "found": "listed as found, usually in a recovery committee or yard inventory",
    "recovered": "the post says the vehicle was returned to its owner",
    "seeking_owner": "someone holding or seeing the vehicle is looking for its owner",
    "spotted": "seen somewhere, not in anyone's custody",
    "for_sale": "offered for sale",
    "reported_missing": "an owner reports the vehicle lost or stolen",
    "in_custody": "held by police or another authority",
    "not_stated": "the post gives no status",
    "other": "any other status",
}
NOT_A_MAKE = {"", "unknown", "none", "null", "n/a", "غير معروف"}
# The make field sometimes holds only what kind of vehicle it is. Those are
# reported as their own kind, so "Pickup" is never read as a manufacturer.
BODY_TYPES = {"PICKUP", "RICKSHAW", "AUTO RICKSHAW", "TUK-TUK", "VAN/MINIBUS", "VAN", "MINIBUS",
              "BUS", "TRUCK", "SEDAN", "MOTORCYCLE", "TRACTOR", "TRAILER", "AMJAD"}
MAKE_ALIASES = {"MERCEDES": "MERCEDES-BENZ", "VW": "VOLKSWAGEN"}


def _connect():
    return sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=60)


def _cell(n: int) -> tuple[str, str]:
    """(published number, note). Small non-zero cells lose their number."""
    if 0 < n < SUPPRESS_BELOW:
        return "", f"1-{SUPPRESS_BELOW - 1}"
    return str(n), ""


def by_location(conn) -> tuple[list[list], list[list]]:
    """Vehicles per state, and how many vehicles each mapping class holds."""
    per_state = collections.defaultdict(set)
    per_class = collections.defaultdict(set)
    stated = set()
    cache: dict[str, geo.Location] = {}
    for key, location in conn.execute(
            f"SELECT DISTINCT {VEHICLE_KEY}, location FROM vehicles "
            f"WHERE location IS NOT NULL AND TRIM(location) <> '' AND {VEHICLE_KEY} IS NOT NULL"):
        loc = cache.get(location)
        if loc is None:
            loc = cache[location] = geo.locate(location)
        stated.add(key)
        if loc.kind in geo.ADMIN1:
            per_state[loc.kind].add(key)
            per_class["state"].add(key)
        elif loc.kind == geo.REGION_ONLY:
            per_class["region_only:" + loc.region].add(key)
        else:
            per_class[loc.kind].add(key)
    total = conn.execute(f"SELECT COUNT(DISTINCT {VEHICLE_KEY}) FROM vehicles").fetchone()[0]

    states = []
    for code in sorted(geo.ADMIN1):
        en, ar = geo.ADMIN1[code]
        n, note = _cell(len(per_state[code]))
        states.append(["SD", code, en, ar, n, note])

    labels = [
        ("state", "mapped to one state"),
        ("region_only:Kordofan", "says Kordofan without naming which of its three states"),
        ("region_only:Darfur", "says Darfur without naming which of its five states"),
        (geo.MULTIPLE, "names places in more than one state"),
        (geo.COUNTRY_ONLY, "says Sudan only"),
        (geo.UNMAPPED, "names no place the gazetteer knows (a building, an office, a village)"),
    ]
    quality = [[k, text, *_cell(len(per_class[k]))] for k, text in labels]
    quality.append(["no_location", "no post about the vehicle gives a location",
                    *_cell(total - len(stated))])
    quality.append(["all_vehicles", "every identified vehicle in the registry", str(total), ""])
    return states, quality


def by_status(conn) -> list[list]:
    sets = collections.defaultdict(set)
    for key, status in conn.execute(
            f"SELECT DISTINCT {VEHICLE_KEY}, LOWER(TRIM(COALESCE(status, 'unknown'))) "
            f"FROM vehicles WHERE {VEHICLE_KEY} IS NOT NULL"):
        sets[STATUS.get(status, "other")].add(key)
    rows = sorted(sets.items(), key=lambda kv: -len(kv[1]))
    return [[k, STATUS_MEANING[k], *_cell(len(v))] for k, v in rows]


def by_make(conn) -> list[list]:
    sets = collections.defaultdict(set)
    for key, make in conn.execute(
            f"SELECT DISTINCT {VEHICLE_KEY}, UPPER(TRIM(COALESCE(make, ''))) "
            f"FROM vehicles WHERE {VEHICLE_KEY} IS NOT NULL"):
        sets[MAKE_ALIASES.get(make, make)].add(key)
    makes, bodies, other, unstated = [], [], set(), set()
    for make, keys in sets.items():
        if make.lower() in NOT_A_MAKE:
            unstated |= keys
        elif make == "OTHER" or len(keys) < MIN_MAKE:
            other |= keys
        elif make in BODY_TYPES:
            bodies.append((make.title(), len(keys)))
        else:
            # short names are initialisms (BYD, KIA, JAC); the rest read better title-cased
            makes.append((make if len(make) <= 3 else make.title(), len(keys)))
    rows = [[label, "make", *_cell(n)] for label, n in sorted(makes, key=lambda kv: -kv[1])]
    rows += [[label, "body_type_only", *_cell(n)] for label, n in sorted(bodies, key=lambda kv: -kv[1])]
    rows.append([f"Other (each under {MIN_MAKE} vehicles)", "other", *_cell(len(other))])
    rows.append(["Not stated", "not_stated", *_cell(len(unstated))])
    return rows


def posts_by_month(conn) -> list[list]:
    rows = conn.execute(
        "SELECT substr(seen_at, 1, 7), substr(source, 1, instr(source || ':', ':') - 1), COUNT(*) "
        "FROM posts GROUP BY 1, 2 ORDER BY 1, 2").fetchall()
    names = {"intake": "sent_to_us_directly"}
    return [[month, names.get(platform, platform or "other"), *_cell(n)] for month, platform, n in rows]


def summary(conn) -> list[list]:
    q = lambda sql: conn.execute(sql).fetchone()[0]
    first, last = conn.execute("SELECT MIN(seen_at), MAX(seen_at) FROM posts").fetchone()
    return [
        ["posts_collected", q("SELECT COUNT(*) FROM posts"),
         "public posts collected from Facebook groups and pages and Telegram channels"],
        ["photos_collected", q("SELECT COUNT(*) FROM media"), "photos attached to those posts"],
        ["photos_transcribed", q("SELECT COUNT(*) FROM media WHERE ocr_at IS NOT NULL "
                                 "AND (ocr_error IS NULL OR ocr_error = '')"),
         "photos read by OCR"],
        ["sighting_rows", q("SELECT COUNT(*) FROM vehicles"),
         "extracted rows; lists are reposted to many groups, so this overstates vehicles"],
        ["identified_vehicles", q(f"SELECT COUNT(DISTINCT {VEHICLE_KEY}) FROM vehicles"),
         "distinct normalised chassis numbers, plus distinct plates on rows with no chassis"],
        ["distinct_chassis", q("SELECT COUNT(DISTINCT chassis_norm) FROM vehicles "
                               "WHERE chassis_norm IS NOT NULL AND chassis_norm <> ''"),
         "distinct normalised chassis numbers"],
        ["first_post_collected", (first or "")[:10], "date collection began"],
        ["latest_post_collected", (last or "")[:10], "date of the newest post in this build"],
    ]


TABLES = {
    "registry_summary.csv": (
        ["indicator", "value", "definition"],
        ["#indicator+code", "#indicator+value", "#description"]),
    "vehicles_by_admin1.csv": (
        ["adm0_pcode", "adm1_pcode", "adm1_name_en", "adm1_name_ar", "vehicles", "vehicles_note"],
        ["#country+code", "#adm1+code", "#adm1+name+en", "#adm1+name+ar",
         "#indicator+vehicles+num", "#meta+suppressed"]),
    "location_mapping_quality.csv": (
        ["mapping_class", "meaning", "vehicles", "vehicles_note"],
        ["#meta+class", "#description", "#indicator+vehicles+num", "#meta+suppressed"]),
    "vehicles_by_status.csv": (
        ["status", "meaning", "vehicles", "vehicles_note"],
        ["#status+code", "#description", "#indicator+vehicles+num", "#meta+suppressed"]),
    "vehicles_by_make.csv": (
        ["make", "kind", "vehicles", "vehicles_note"],
        ["#item+make", "#item+type", "#indicator+vehicles+num", "#meta+suppressed"]),
    "posts_by_month_platform.csv": (
        ["month_collected", "platform", "posts", "posts_note"],
        ["#date+month", "#meta+platform", "#indicator+posts+num", "#meta+suppressed"]),
}


def build() -> dict[str, list[list]]:
    conn = _connect()
    try:
        states, quality = by_location(conn)
        return {
            "registry_summary.csv": summary(conn),
            "vehicles_by_admin1.csv": states,
            "location_mapping_quality.csv": quality,
            "vehicles_by_status.csv": by_status(conn),
            "vehicles_by_make.csv": by_make(conn),
            "posts_by_month_platform.csv": posts_by_month(conn),
        }
    finally:
        conn.close()


def to_csv(name: str, rows: list[list]) -> str:
    header, hxl = TABLES[name]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerow(hxl)
    w.writerows(rows)
    return buf.getvalue()


def main() -> None:
    data = build()
    if "--print" in sys.argv:
        for name, rows in data.items():
            print(f"--- {name}")
            print(to_csv(name, rows))
        return
    OUT.mkdir(exist_ok=True)
    for name, rows in data.items():
        (OUT / name).write_text(to_csv(name, rows), encoding="utf-8", newline="")
    meta = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "suppress_below": SUPPRESS_BELOW,
        "boundaries": "OCHA COD-AB Sudan v03, https://data.humdata.org/dataset/cod-ab-sdn",
        "files": {name: len(rows) for name, rows in data.items()},
    }
    (OUT / "build.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    print(f"open-data written: {len(data)} tables, generated {meta['generated_at']}")


if __name__ == "__main__":
    main()
