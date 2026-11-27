"""Repair stored vehicle rows whose chassis or plate field holds several numbers.

Before 2026-11-25 a lane could store "A, B" or "A / B" in one chassis field;
`norm_chassis` glued them into a number written nowhere. Ingest now splits such
a field (`db.expand_entry`); this applies the same rule to the rows already in
the registry. Each affected row is replaced by one row per identifier, every
other column kept (including extracted_at and low_confidence).

    python scripts\\split_glued_identifiers.py            # dry run: counts only
    python scripts\\split_glued_identifiers.py --apply    # backup, then rewrite

The originals are written to data/glued_identifier_rows_backup_<date>.json
before anything changes. Safe to run again: a repaired row no longer matches.
"""
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from carhunt import db                                       # noqa: E402
from carhunt.config import DATA_DIR                          # noqa: E402
from carhunt.normalize import norm_chassis, norm_plate       # noqa: E402

COLS = ["id", "post_id", "make", "model", "color", "year", "chassis", "chassis_norm", "plate",
        "plate_norm", "status", "location", "contact", "description", "extracted_at",
        "model_key", "low_confidence"]
CANDIDATES = (
    "SELECT " + ", ".join(COLS) + " FROM vehicles WHERE "
    + " OR ".join(f"chassis LIKE '%{s}%'" for s in (",", "/", "او", "أو", "،", ";", "|", " or ", " OR ", "\\"))
    + " OR " + " OR ".join(f"plate LIKE '%{s}%'" for s in (",", "،", "او", "أو", ";", "|", " or ", " OR "))
    # one number written twice, in Arabic and in Latin digits
    + " OR (chassis GLOB '*[٠-٩]*' AND chassis GLOB '*[0-9]*')")


def plan(conn) -> list[tuple[dict, list[dict]]]:
    out = []
    for r in conn.execute(CANDIDATES):
        row = dict(zip(COLS, r))
        new = db.expand_entry(row)
        if len(new) == 1 and new[0].get("chassis") == row["chassis"] and new[0].get("plate") == row["plate"]:
            continue
        out.append((row, new))
    return out


def main() -> int:
    apply = "--apply" in sys.argv
    conn = db.connect()
    todo = plan(conn)
    conn.close()
    after = sum(len(n) for _, n in todo)
    print(f"{len(todo)} row(s) hold several identifiers -> {after} row(s) after the split "
          f"({len({r['post_id'] for r, _ in todo})} posts)")
    if not apply or not todo:
        return 0
    stamp = datetime.date.today().isoformat()
    backup = DATA_DIR / f"glued_identifier_rows_backup_{stamp}.json"
    if backup.exists():
        backup = DATA_DIR / f"glued_identifier_rows_backup_{stamp}_{datetime.datetime.now():%H%M%S}.json"
    backup.write_text(json.dumps([r for r, _ in todo], ensure_ascii=False, indent=1), encoding="utf-8")
    print("backup:", backup)
    insert = ("INSERT INTO vehicles (" + ", ".join(COLS[1:]) + ") VALUES ("
              + ",".join("?" * (len(COLS) - 1)) + ")")
    done = 0
    for i in range(0, len(todo), 200):                       # short transactions: lanes are writing
        with db.write_tx() as w:
            for row, new in todo[i:i + 200]:
                still = w.execute("SELECT chassis, plate FROM vehicles WHERE id = ?", (row["id"],)).fetchone()
                if not still or (still[0], still[1]) != (row["chassis"], row["plate"]):
                    continue                                 # re-ingested meanwhile: leave it
                w.execute("DELETE FROM vehicles WHERE id = ?", (row["id"],))
                for n in new:
                    n = dict(n, chassis_norm=norm_chassis(n.get("chassis") or "") or None,
                             plate_norm=norm_plate(n.get("plate") or "") or None)
                    w.execute(insert, [n.get(c) for c in COLS[1:]])
                done += 1
    print(f"rewrote {done} row(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
