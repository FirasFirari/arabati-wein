"""Ingest identifiers read from post IMAGES by a local vision pass.

Different from `ingest_local_verdicts.py`: these chassis/plate values were read
from a photograph, not from the post text, so the text-grounding check does not
apply. Two safeguards replace it:

  * the SCORE is computed here, deterministically, from whether the identifier
    actually matches the target car — never taken from a model's opinion. This
    keeps lesson #0 ("the search is for an IDENTIFIER, not a resemblance")
    enforced in code rather than in a prompt.
  * a VIN is only accepted if it looks like a VIN (11-17 alphanumerics) and a
    plate only if it carries a digit run; free-text notes are dropped.

Rows land with from_photo set by the existing exporter logic (the post has
media), so the public page already marks them "من صورة ⚠️".

Usage:
    python scripts/ingest_image_findings.py <image_out.json> [--dry-run]
"""
import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from carhunt import db
from carhunt.config import load_config
from carhunt.normalize import chassis_overlap, norm_chassis, norm_plate

VIN_RE = re.compile(r"^[A-Z0-9]{11,17}$")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", type=pathlib.Path)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tier", default="local_claude_vision")
    a = ap.parse_args()

    car = load_config()["car"]
    target, tplate = car["vin"], car.get("plate", "")
    db.init()
    conn = db.connect()

    written = vehicles = alerts = skipped = 0
    conflicts = []
    for path in a.paths:
        for r in json.loads(path.read_text(encoding="utf-8")):
            pid = r.get("id")
            row = conn.execute("SELECT id, text FROM posts WHERE id = ?", (pid,)).fetchone()
            if row is None:
                skipped += 1
                continue

            chassis = [c for c in (r.get("chassis_numbers") or [])
                       if VIN_RE.match(norm_chassis(str(c)))]
            plates = [p for p in (r.get("plates") or []) if re.search(r"\d", str(p))]
            if not chassis and not plates:
                skipped += 1
                continue

            # deterministic scoring — identifier match only, never resemblance
            score, reasons = 20, []
            for c in chassis:
                ov = chassis_overlap(target, c)
                if ov >= 6:
                    score = 95
                    reasons.append(f"CHASSIS MATCH from image ({ov} chars): {c}")
            for p in plates:
                if norm_plate(p) and norm_plate(p) == norm_plate(tplate):
                    score = 95
                    reasons.append(f"PLATE MATCH from image: {p}")
            if not reasons:
                reasons.append(
                    f"identifiers read from image ({len(chassis)} chassis, "
                    f"{len(plates)} plate) — no match to the target car")

            # MERGE, never replace: db.add_vehicles() deletes a post's existing
            # rows, and those rows carry make/model/colour the image pass has no
            # way to know. Keep them, add what the image revealed, and report any
            # identifier the two passes read differently rather than picking a
            # winner silently — a screen tier misreading digits is exactly the
            # failure this re-check exists to find.
            existing = [dict(x) for x in conn.execute(
                "SELECT make, model, color, year, chassis, plate, status, "
                "location, contact, description FROM vehicles WHERE post_id = ?",
                (pid,)).fetchall()]
            have_ch = {norm_chassis(x["chassis"] or "") for x in existing if x["chassis"]}
            have_pl = {norm_plate(x["plate"] or "") for x in existing if x["plate"]}

            veh = list(existing)
            for c in chassis:
                nc = norm_chassis(str(c))
                for old_nc in have_ch:
                    if old_nc and nc != old_nc and (
                            old_nc[-6:] == nc[-6:] or old_nc[:6] == nc[:6]):
                        conflicts.append((pid, old_nc, nc))
                if nc in have_ch:
                    continue
                veh.append({"chassis": str(c), "status": "spotted",
                            "description": f"read from image: {r.get('image_kind')}"})
            for p in plates:
                if norm_plate(str(p)) in have_pl:
                    continue
                veh.append({"plate": str(p), "status": "spotted",
                            "description": f"read from image: {r.get('image_kind')}"})

            verdict = {
                "is_car_post": True,
                "model_match": score >= 95,
                "score": score,
                "reasons": "; ".join(reasons),
                "tier": a.tier,
                "extracted": {"vin": chassis[0] if chassis else None,
                              "plate": plates[0] if plates else None,
                              "described_vehicle": None, "location": None,
                              "price": None, "contact": None},
                "vehicles": veh,
            }
            if score >= 95:
                alerts += 1
                url = conn.execute("SELECT url FROM posts WHERE id = ?", (pid,)).fetchone()["url"]
                print(f"  !! post {pid}: {verdict['reasons']}\n     {url}")
            if not a.dry_run:
                db.save_analysis(pid, verdict)
            written += 1
            vehicles += len(veh)

    verb = "would write" if a.dry_run else "wrote"
    print(f"\n{verb} {written} image verdicts, {vehicles} vehicle rows; "
          f"skipped {skipped} with no legible identifier")
    print(f"target matches: {alerts}")
    if conflicts:
        print("")
        print("IDENTIFIER CONFLICTS - the earlier pass and the image pass read")
        print(f"these differently ({len(conflicts)}). Both rows are kept; verify")
        print("the image by hand before trusting either:")
        for pid, old_v, new_v in conflicts:
            print(f"   post {pid}: stored {old_v}  vs image {new_v}")


if __name__ == "__main__":
    main()
