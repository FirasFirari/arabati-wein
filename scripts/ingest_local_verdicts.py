"""Write verdicts produced by a local (subscription) subagent back into the DB.

The subagent pass produces a JSON array of {"id": <post_id>, "verdict": {...}}
in the same shape the API analyzer returns. This ingests them through the
normal `db.save_analysis` path, so vehicles land in the registry exactly as
they would from an API run.

Usage:
    python scripts/ingest_local_verdicts.py <verdicts.json> [--tier local_claude] [--dry-run]
"""
import argparse
import json
import pathlib
import re
import sys
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from carhunt import db
from carhunt.normalize import has_prose, strip_prose

# This script prints Arabic — post text, plate values like '12345 خ1', the
# identifiers the grounding gate nulls. The Windows console is cp1252 and
# raises UnicodeEncodeError on the first one. Here that is not cosmetic: the
# crash lands INSIDE the ingest loop, after some posts have already been
# written, leaving a half-ingested batch. It did exactly that on wave 1 —
# batch 021 stopped at 64 of 121 posts.
import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


REQUIRED = {"is_car_post", "score", "reasons"}

AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
# identifier fields that must be traceable to the post's own text
GROUNDED_FIELDS = ("chassis", "plate", "contact", "price")


def _digits(value) -> str:
    return re.sub(r"\D", "", unicodedata.normalize("NFKC", str(value or "")).translate(AR_DIGITS))


def strip_ungrounded(verdict: dict, text: str) -> list[str]:
    """Null any identifier whose digits are not in the post's own text.

    ONLY VALID FOR TEXT-ONLY POSTS. An identifier read off a photographed VIN
    plate is by definition absent from the post text, so running this on a
    vision verdict deletes exactly what the vision pass exists to find. It did:
    251 image-derived identifiers were nulled on the first vision ingest before
    the caller learned to skip posts that have media. See the call site.

    For text-only posts, an identifier that is not in the text was
    not read — it was inferred, most often carried over from a neighbouring
    post by the same author. A model has already emitted 117 vehicle rows
    carrying a phone number that appears nowhere in their post, and a
    coverage check cannot see that. Nulling is always the safe repair: a
    missing field costs a family one lookup, a wrong one sends them to a
    stranger.
    """
    src = _digits(text)
    dropped = []
    for veh in (verdict.get("vehicles") or []):
        for f in GROUNDED_FIELDS:
            if not _grounded(veh.get(f), src):
                dropped.append(f"vehicles.{f}={veh.get(f)!r}")
                veh[f] = None
    ex = verdict.get("extracted") or {}
    for f in ("vin", "plate", "contact", "price"):
        if not _grounded(ex.get(f), src):
            dropped.append(f"extracted.{f}={ex.get(f)!r}")
            ex[f] = None
    return dropped


# Digit runs this short are structure, not identity: the "4" of a plate
# region code خ4, the "15" of a model year. Requiring them to be grounded
# says nothing (a single digit is in almost every post), and requiring the
# WHOLE field to be one contiguous run is what broke below. Runs at or above
# this length are identifier-shaped and every one of them must be grounded.
_MIN_GROUNDED_RUN = 3


def _grounded(value, src: str) -> bool:
    """Is every identifier-shaped digit run in `value` present in the source?

    The original check concatenated a field's digits and required that exact
    string inside the post's digit stream. That is correct for a field holding
    ONE identifier written the same way as the post, and wrong in two shapes
    that occur constantly — measured on the Gemini benchmark 2026-08-31, where
    it nulled REAL data on 16% of posts:

      * multi-value fields. `extracted.contact='09XXXXXXXX, 09XXXXXXXX'` —
        both numbers are in the post, the concatenation of them is not.
      * reordered tokens. A post writes `رقم اللوحة خ4/ 13297`; a model
        normalises it to the standard `13297 خ4`. Same plate, digits now in a
        different order, so the contiguous check fails.

    Grounding each run separately fixes both WITHOUT weakening the gate: a
    fabricated chassis is still a 5-9 digit run that appears nowhere in the
    post, and it is still nulled. That is the case the gate exists for — 106
    invented identifiers caught, zero self-reported — so it is the case the
    tests below must keep failing closed.
    """
    raw = str(value or "")
    if not _digits(raw):
        return True  # nothing numeric to ground
    runs = [r for r in re.findall(r"\d+", _norm_digits(raw))
            if len(r) >= _MIN_GROUNDED_RUN]
    if not runs:
        # Only short runs — e.g. a bare region code. Fall back to the strict
        # contiguous check rather than waving it through on no evidence.
        return _digits(raw) in src
    return all(r in src for r in runs)


def _norm_digits(value: str) -> str:
    """NFKC + Arabic-Indic folded to ASCII, punctuation and letters intact.

    `_digits()` strips every non-digit, which destroys the separators that
    tell one identifier from the next. This keeps them so runs stay distinct.
    """
    return unicodedata.normalize("NFKC", str(value or "")).translate(AR_DIGITS)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=pathlib.Path)
    ap.add_argument("--tier", default="local_claude")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="re-ingest posts that already carry a score, replacing "
                         "the stored verdict and vehicle rows. For correcting a "
                         "bad earlier pass, not for routine use.")
    ap.add_argument("--no-shrink", action="store_true",
                    help="with --force, refuse to replace a post whose stored "
                         "vehicle rows outnumber the incoming ones. Re-extraction "
                         "exists to recover rows a truncated pass missed; it must "
                         "never be able to delete rows a better pass already found.")
    ap.add_argument("--batch", type=pathlib.Path,
                    help="the batch file this run was produced from; every post "
                         "id in it must appear in the verdicts, or nothing is "
                         "written. A subagent can stop early and still report "
                         "success, so coverage is verified, never trusted.")
    a = ap.parse_args()

    data = json.loads(a.path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("results") or data.get("verdicts") or []

    if a.batch:
        expected = {p["id"] for p in json.loads(a.batch.read_text(encoding="utf-8"))}
        got = {i.get("id") for i in data if isinstance(i, dict)}
        missing = sorted(expected - got)
        if missing:
            shown = missing[:20]
            tail = " ..." if len(missing) > 20 else ""
            print(f"COVERAGE FAILURE: {len(missing)} of {len(expected)} posts "
                  f"missing from {a.path.name}")
            print(f"  {shown}{tail}")
            print("Nothing written. Re-run those posts before ingesting.")
            raise SystemExit(1)
        print(f"coverage verified: {len(expected)}/{len(expected)} posts present")

    db.init()
    conn = db.connect()
    written = skipped = vehicles = high = ungrounded = shrank = image_only = 0
    prose_fixed = 0
    for item in data:
        pid, v = item.get("id"), item.get("verdict")
        # Agents write one of two shapes: {"id":.., "verdict":{..}} or the
        # verdict fields flat alongside the id. An agent that used the flat
        # shape had all 195 of its posts silently rejected as "malformed" while
        # the run still reported success and parked the files - 3,277 vehicles
        # dropped. Accept both.
        if not isinstance(v, dict) and REQUIRED.issubset(item):
            v = {k: val for k, val in item.items() if k != "id"}
        if not isinstance(v, dict) or not REQUIRED.issubset(v):
            skipped += 1
            print(f"  skip {pid}: malformed verdict")
            continue
        row = conn.execute("SELECT score FROM posts WHERE id = ?", (pid,)).fetchone()
        if row is None:
            skipped += 1
            print(f"  skip {pid}: no such post")
            continue
        if row["score"] is not None and not a.force:
            skipped += 1
            print(f"  skip {pid}: already scored ({row['score']})")
            continue
        if row["score"] is not None:
            prior = conn.execute(
                "SELECT COUNT(*) c FROM vehicles WHERE post_id = ?", (pid,)).fetchone()["c"]
            incoming = len(v.get("vehicles") or [])
            if a.no_shrink and incoming < prior:
                skipped += 1
                shrank += 1
                print(f"  SKIP post {pid}: incoming {incoming} vehicles < stored "
                      f"{prior} — refusing to shrink")
                continue
            print(f"  replacing post {pid}: score {row['score']}, "
                  f"{prior} rows -> {incoming}")
        v = json.loads(json.dumps(v))  # deep copy: we edit nested vehicle dicts
        v["tier"] = a.tier
        # grounding gate — the coverage check above cannot see a fabricated
        # identifier, so strip anything not traceable to this post's own text
        # commentary guard: identifiers must hold an identifier and nothing
        # else, on every tier. norm_* strips punctuation, so prose left in the
        # field is folded into the value and can fake - or hide - a match.
        for veh in (v.get("vehicles") or []):
            for f in ("chassis", "plate"):
                val = veh.get(f)
                if val and has_prose(val):
                    cleaned = strip_prose(val)
                    if cleaned != val:
                        veh[f] = cleaned
                        veh["description"] = ((veh.get("description") or "")
                                              + (" | " if veh.get("description") else "")
                                              + f"original {f}: {val}")
                        prose_fixed += 1
        post_text = conn.execute(
            "SELECT text FROM posts WHERE id = ?", (pid,)).fetchone()["text"] or ""
        # Google Vision OCR of this post's photos counts as the post's own
        # text for grounding. This is what the OCR sweep was FOR: a chassis
        # stamped on a compliance plate has always been ungroundable, because
        # it is absent from the post body by definition, so the gate had to be
        # switched off for every post with an image. With the OCR stored, that
        # number has something to be checked against and the gate covers the
        # image half of the corpus for the first time. Without this line an
        # ingest would null every identifier the OCR pass just recovered —
        # 537 of them in wave 1 alone.
        ocr_rows = conn.execute(
            "SELECT ocr_text FROM media WHERE post_id = ? AND ocr_text IS NOT NULL "
            "AND ocr_text <> ''", (pid,)).fetchall()
        if ocr_rows:
            post_text = post_text + chr(10) + chr(10).join(r[0] for r in ocr_rows)
        # A vision verdict on a post that HAS images cannot be text-grounded:
        # the whole point is reading numbers off the photo. Grounding such a
        # post nulls every legitimate find. Text-only posts in a vision batch
        # (images failed to download) still get the gate.
        n_media = conn.execute(
            "SELECT COUNT(*) c FROM media WHERE post_id = ?", (pid,)).fetchone()["c"]
        if "vision" in a.tier and n_media:
            image_only += len([1 for veh in (v.get("vehicles") or [])
                               for f in GROUNDED_FIELDS if veh.get(f)])
            dropped = []
        else:
            dropped = strip_ungrounded(v, post_text)
        if dropped:
            ungrounded += len(dropped)
            print(f"  post {pid}: nulled {len(dropped)} ungrounded value(s)")
            for d in dropped[:3]:
                print(f"      {d}")
            if len(dropped) > 3:
                print(f"      ... and {len(dropped) - 3} more")
        vehicles += len(v.get("vehicles") or [])
        if (v.get("score") or 0) >= 70:
            high += 1
            url = conn.execute("SELECT url FROM posts WHERE id = ?", (pid,)).fetchone()["url"]
            print(f"  !! post {pid} scored {v['score']} — VERIFY: {url}")
        if not a.dry_run:
            db.save_analysis(pid, v)
        written += 1

    verb = "would write" if a.dry_run else "wrote"
    print(f"\n{verb} {written} verdicts, {vehicles} vehicles; skipped {skipped}")
    print(f"posts scoring >=70 needing manual verification: {high}")
    if shrank:
        print(f"posts left untouched because the new pass had FEWER vehicles: {shrank}")
    if prose_fixed:
        print(f"commentary stripped from identifier fields: {prose_fixed}")
    if image_only:
        print(f"image-derived identifiers kept unverified: {image_only} "
              f"(vision tier, grounding gate not applicable)")
    if ungrounded:
        print(f"UNGROUNDED IDENTIFIERS NULLED: {ungrounded} — a model emitted "
              f"values not present in the post's own text")
    remaining = conn.execute("SELECT COUNT(*) c FROM posts WHERE score IS NULL").fetchone()["c"]
    print(f"unscored posts remaining: {remaining}")


if __name__ == "__main__":
    main()
