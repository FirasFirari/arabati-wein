"""Assert that standing watches match the RIGHT numbers.

    python scripts\\check_watch_rule.py

Run after touching `carhunt/watches.py` or `is_plate_shaped` / `plate_variants`
in `carhunt/normalize.py`. Exits non-zero on the first failure.

Why it exists: on 2026-10-08 the watch "41019 خ 9" (a plate) raised two raw
leads on a missing man's PASSPORT. `norm_chassis` glued the plate into
"410199", and the passport's machine-readable line ends ...M2410199, so it
matched as a "chassis suffix". The same day, the one real sighting, a list line
reading "41019 / خ #", was invisible to the registry scan because the كشف
parser files a lone plate as the row's chassis.

Cases that involve the configured car are derived from config.yaml; everything
else uses synthetic numbers that belong to nobody.
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
MRZ = "P062951945SDN9601019M2410199"


def main() -> None:
    car = load_config()["car"]
    vin = (car.get("vin") or "").strip()
    tail = vin[-7:]
    plate = car.get("plate") or ""
    serial = re.search(r"\d{4,6}", plate).group(0)
    region = _region_digit(plate) or "1"
    glued_ar = ("خ" + region + serial).translate(AR)

    # (label, watch identifier, stored chassis_norm, stored plate_norm, should match?)
    registry = [
        ("plate watch, plate column",          "41019 خ 9", None, "41019", True),
        ("plate watch, plate filed as chassis", "41019 خ 9", "41019", None, True),
        ("plate watch vs passport line",       "41019 خ 9", MRZ, None, False),
        ("plate watch vs glued number",        "41019 خ 9", "410199", None, False),
        ("plate watch, serial only a suffix",  "41019 خ 9", "7741019", None, False),
        ("chassis watch, exact",               "480966", "480966", None, True),
        ("chassis watch, suffix of long VIN",  tail, vin, None, True),
        ("chassis watch, Arabic digits",       "٧٦١٤٣٣٧", "7614337", None, True),
        ("chassis watch, unrelated",           "480966", "123456", None, False),
    ]

    # (label, watch identifier, raw token, should match?)
    raw = [
        ("passport line, the 2026-10-08 false lead", "41019 خ 9", MRZ, False),
        ("plate serial as its own token",            "41019 خ 9", "41019", True),
        ("chassis in raw text",                      "480966", "480966", True),
        ("chassis, leading zero dropped",            "098939", "98939", True),
        ("phone number never counts",                "2345678", "0912345678", False),
    ]

    plate_shaped = [
        ("41019 خ 9", True), ("31305 خ 1", True), (glued_ar, True),
        ("40141خ4", True), ("218 / ج ك 9", True), ("لوحة 41019", True),
        (f"شاسي {tail}", False), ("٧٦١٤٣٣٧", False), ("JTMHU01J3L4198780", False),
        (f"{tail} او {tail[1:]}", False), ("كورولا 2015", False), ("480966", False),
    ]

    failures = 0
    for value, want in plate_shaped:
        got = is_plate_shaped(value)
        ok = got == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} plate-shaped case: {got}")
    for label, ident, cn, pn, want in registry:
        qn, qp = readings(ident)
        why = match_reason(qn, qp, norm_chassis(cn) if cn else cn, pn)
        ok = bool(why) == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} registry: {label}: {bool(why)}")
    for label, ident, token, want in raw:
        qn, qp = readings(ident)
        why = raw_reason(qn, qp, norm_chassis(token))
        ok = bool(why) == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} raw: {label}: {bool(why)}")
    if failures:
        sys.exit(f"\n{failures} FAILED")
    print("\nall passed")


if __name__ == "__main__":
    main()
