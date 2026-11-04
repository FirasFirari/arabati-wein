"""Bring rows ALREADY in the registry up to the rules new rows now pass.

    python scripts\\regate_registry.py            # dry run: counts only
    python scripts\\regate_registry.py --apply    # write (take a backup first)

Never deletes a value: every repair either corrects a derived column or ADDS
a doubt that the published page then shows. Four passes:

1. chassis_norm recomputed wherever it differs from normalize.norm_chassis
   (the O/I/Q fold for 17-character VINs, plus a few stale rows).
2. كشف rows whose plate shape the parser could not resolve ("murky") get
   low_confidence; the parser always knew, the ingest dropped the flag.
3. Identifiers that the grounding gate (carhunt/gates.py) cannot find in the
   post or its trusted OCR get low_confidence -- FLAGGED, not nulled: rows
   from lanes that were never gated (live Telegram, batch screen, photo lanes)
   may be the only record of a car, and a human can still open the photo.
4. Posts that were scored but lost their rows (claimed vehicles > 0, stored
   0, not a duplicate -- post 171728 after a lock) are put back in the queue.
"""
import argparse
import collections
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from carhunt import db, kashf  # noqa: E402
from carhunt.gates import DOUBTFUL_OCR, UNCHECKED_PHOTO, grounded, source_runs  # noqa: E402
from carhunt.normalize import norm_chassis  # noqa: E402

NOT_IN_SOURCE = "identifier not found in the post or its OCR text"
DOUBTFUL = ("+disagree", "+unverified")


def ro():
    con = sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True, timeout=60)
    con.row_factory = sqlite3.Row
    return con


def write(sql: str, rows: list[tuple], apply: bool, chunk: int = 2000) -> None:
    if not apply:
        return
    for i in range(0, len(rows), chunk):
        with db.write_tx() as w:
            w.executemany(sql, rows[i:i + chunk])


def pass_norms(con, apply):
    fix = [(norm_chassis(r["chassis"]) or None, r["id"]) for r in con.execute(
        "SELECT id, chassis, chassis_norm FROM vehicles WHERE chassis IS NOT NULL")
        if (norm_chassis(r["chassis"]) or None) != r["chassis_norm"]]
    write("UPDATE vehicles SET chassis_norm = ? WHERE id = ?", fix, apply)
    return len(fix)


def pass_murky(con, apply):
    tier = {r[0]: r[1] for r in con.execute(
        "SELECT id, json_extract(verdict, '$.tier') FROM posts WHERE verdict IS NOT NULL")}
    murky = collections.defaultdict(set)
    for r in con.execute("SELECT id, text FROM posts"):
        if tier.get(r["id"]) == "kashf_parser" and r["text"]:
            for v in kashf.parse(r["text"])["vehicles"]:
                if v.get("murky"):
                    murky[r["id"]].add(v["chassis"])
    fix = []
    for pid, chs in murky.items():
        for row in con.execute("SELECT id, chassis FROM vehicles WHERE post_id = ? "
                               "AND low_confidence IS NULL", (pid,)):
            if row["chassis"] in chs:
                fix.append(("plate shape the list parser could not resolve", row["id"]))
    write("UPDATE vehicles SET low_confidence = ? WHERE id = ? AND low_confidence IS NULL", fix, apply)
    return len(fix)


def pass_grounding(con, apply):
    by_reason = collections.Counter()
    fix = []
    last_pid, runs, doubtful, unread = None, set(), set(), False
    for v in con.execute("SELECT id, post_id, chassis, plate, contact FROM vehicles "
                         "WHERE low_confidence IS NULL ORDER BY post_id"):
        if v["post_id"] != last_pid:
            last_pid = v["post_id"]
            p = con.execute("SELECT text FROM posts WHERE id = ?", (last_pid,)).fetchone()
            media = con.execute("SELECT ocr_text, ocr_at, COALESCE(ocr_engine,'') e FROM media "
                                "WHERE post_id = ?", (last_pid,)).fetchall()
            trusted = "\n".join(m["ocr_text"] or "" for m in media if not m["e"].endswith(DOUBTFUL))
            runs = source_runs(((p["text"] if p else None) or "") + "\n" + trusted)
            doubtful = source_runs("\n".join(m["ocr_text"] or "" for m in media if m["e"].endswith(DOUBTFUL)))
            unread = any(m["ocr_at"] is None for m in media)
        reason = None
        for f in ("chassis", "plate", "contact"):
            val = v[f]
            if not val or grounded(val, runs):
                continue
            reason = (DOUBTFUL_OCR if grounded(val, doubtful) else
                      UNCHECKED_PHOTO if unread else NOT_IN_SOURCE)
            break
        if reason:
            by_reason[reason] += 1
            fix.append((reason, v["id"]))
    write("UPDATE vehicles SET low_confidence = ? WHERE id = ? AND low_confidence IS NULL", fix, apply)
    return dict(by_reason)


def pass_requeue(con, apply):
    stored = collections.Counter(r[0] for r in con.execute("SELECT post_id FROM vehicles"))
    lost = [r["id"] for r in con.execute(
        "SELECT id FROM posts WHERE score IS NOT NULL "
        "AND COALESCE(json_extract(verdict, '$.tier'), '') NOT IN ('duplicate') "
        "AND COALESCE(json_extract(verdict, '$.vehicles_copied_from_duplicate'), 0) = 0 "
        "AND COALESCE(json_array_length(json_extract(verdict, '$.vehicles')), 0) > 0 "
        # a human looked and ruled it out: its rows were removed on purpose
        "AND json_type(verdict, '$.verified_by_eye') IS NULL")
        if not stored.get(r["id"])]
    # the verdict is KEPT; only the score goes, which puts the post back in
    # the queue (db.unanalyzed selects score IS NULL)
    write("UPDATE posts SET score = NULL, extracted = 0 WHERE id = ?",
          [(i,) for i in lost], apply)
    return lost


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    db.init()
    con = ro()
    mode = "APPLY" if args.apply else "dry run"
    print(f"[{mode}] 1. chassis_norm recomputed: {pass_norms(con, args.apply):,}")
    print(f"[{mode}] 2. murky كشف rows flagged: {pass_murky(con, args.apply):,}")
    print(f"[{mode}] 3. identifiers the gate cannot ground, flagged: "
          f"{json.dumps(pass_grounding(con, args.apply), ensure_ascii=False)}")
    lost = pass_requeue(con, args.apply)
    print(f"[{mode}] 4. scored posts with lost rows re-queued: {len(lost)} {lost[:10]}")
    con.close()


if __name__ == "__main__":
    main()
