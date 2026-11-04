"""Assert that standing watches match the RIGHT numbers.

    python scripts\\check_watch_rule.py      # against the configured car
    python -m pytest tests/test_watch_rule.py  # same cases, fictional car

Run after touching `carhunt/watches.py` or `is_plate_shaped` / `plate_variants`
in `carhunt/normalize.py`. Exits non-zero on the first failure.

Why it exists: on 2026-10-08 a PLATE watch (serial + region marker) raised two
raw leads on a missing man's PASSPORT. `norm_chassis` glued the plate's serial
and region digit into one run, and the passport's machine-readable line ended
in exactly that run, so it matched as a "chassis suffix". The same day, the one
real sighting, a list line carrying the plate with its region digit
unreadable, was invisible to the registry scan because the كشف parser files a
lone plate as the row's chassis.

Every value below is SYNTHETIC: the plate, the passport-shaped line and the
VINs belong to nobody. Real watch registrations and real documents must never
appear here -- this file is public. Cases that involve the configured car are
derived from the `car:` block.
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from carhunt.config import load_config
from carhunt.normalize import is_plate_shaped, norm_chassis
from carhunt.prefilter import _region_digit
from carhunt.watches import match_reason, raw_reason, readings

AR = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")

# A synthetic plate watch and a passport-shaped line (machine-readable zone:
# document no., nationality, dates, sex) that ENDS in the plate's serial glued
# to its region digit -- the exact shape of the 2026-10-08 false lead.
PLATE_WATCH = "52731 خ 3"
PLATE_SERIAL = "52731"
PLATE_GLUED = "527313"
MRZ = "P000000000XXX0000000M2" + PLATE_GLUED

# A synthetic VIN watch, its 8-character make/model prefix, and a sister car
# off the same line (shares everything but the serial).
VIN_WATCH = "JM0TEST4AGU100284"
VIN_PREFIX = "JM0TEST4"
VIN_SISTER = "JM0TEST4AGU100953"


def build_cases(car: dict):
    """(plate_shaped, registry, raw) case lists for the configured car."""
    vin = (car.get("vin") or "").strip()
    tail = vin[-7:]
    plate = car.get("plate") or ""
    serial = re.search(r"\d{4,6}", plate).group(0)
    region = _region_digit(plate) or "1"
    glued_ar = ("خ" + region + serial).translate(AR)

    # (label, watch identifier, stored chassis_norm, stored plate_norm, should match?)
    registry = [
        ("plate watch, plate column",          PLATE_WATCH, None, PLATE_SERIAL, True),
        ("plate watch, plate filed as chassis", PLATE_WATCH, PLATE_SERIAL, None, True),
        ("plate watch vs passport line",       PLATE_WATCH, MRZ, None, False),
        ("plate watch vs glued number",        PLATE_WATCH, PLATE_GLUED, None, False),
        ("plate watch, serial only a suffix",  PLATE_WATCH, "77" + PLATE_SERIAL, None, False),
        ("chassis watch, exact",               "613572", "613572", None, True),
        ("chassis watch, suffix of long VIN",  tail, vin, None, True),
        ("chassis watch, Arabic digits",       "٨٢٦٤٠١٩", "8264019", None, True),
        ("chassis watch, unrelated",           "613572", "123456", None, False),
        ("VIN watch vs its 8-char make/model prefix (2026-10-27 false lead)",
                                               VIN_WATCH, VIN_PREFIX, None, False),
        ("VIN watch vs a sister car's full VIN", VIN_WATCH, VIN_SISTER, None, False),
        ("numeric fragment with 6 digits still counts", "839215", "8392157", None, True),
    ]

    # (label, watch identifier, raw token, should match?)
    raw = [
        ("passport line, the 2026-10-08 false lead", PLATE_WATCH, MRZ, False),
        ("plate serial as its own token",            PLATE_WATCH, PLATE_SERIAL, True),
        ("chassis in raw text",                      "613572", "613572", True),
        ("chassis, leading zero dropped",            "061724", "61724", True),
        ("phone number never counts",                "2345678", "0912345678", False),
        ("VIN prefix token in alt-text (2026-10-27)",  VIN_WATCH, VIN_PREFIX, False),
        ("VIN serial tail in raw text",              VIN_WATCH, VIN_WATCH[-9:], True),
    ]

    # (value, is it plate-shaped?)
    plate_shaped = [
        (PLATE_WATCH, True), ("60218 خ 4", True), (glued_ar, True),
        ("70352خ6", True), ("315 / ج ك 2", True), ("لوحة " + PLATE_SERIAL, True),
        (f"شاسي {tail}", False), ("٨٢٦٤٠١٩", False), ("JTTEST01J3L410078", False),
        (f"{tail} او {tail[1:]}", False), ("كورولا 2015", False), ("613572", False),
    ]
    return plate_shaped, registry, raw


def run(car: dict) -> list[str]:
    """Labels of every failing case (empty = all pass)."""
    plate_shaped, registry, raw = build_cases(car)
    failed = []
    for value, want in plate_shaped:
        if is_plate_shaped(value) != want:
            failed.append(f"plate-shaped: {value!r} should be {want}")
    for label, ident, cn, pn, want in registry:
        qn, qp = readings(ident)
        why = match_reason(qn, qp, norm_chassis(cn) if cn else cn, pn)
        if bool(why) != want:
            failed.append(f"registry: {label}")
    for label, ident, token, want in raw:
        qn, qp = readings(ident)
        if bool(raw_reason(qn, qp, norm_chassis(token))) != want:
            failed.append(f"raw: {label}")
    return failed


def main() -> None:
    failed = run(load_config()["car"])
    for line in failed:
        print("FAIL", line)
    if failed:
        sys.exit(f"\n{len(failed)} FAILED")
    print("all passed")


if __name__ == "__main__":
    main()
