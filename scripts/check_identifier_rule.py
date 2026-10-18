"""Assert that the deterministic identifier scan matches the RIGHT numbers.

    python scripts\\check_identifier_rule.py

`identifier_hit` is the only check in this project that can declare a match on
its own authority — it writes score 95 and puts a post in "Verified alerts"
with no model in the loop. So when it is too loose, it does not produce a
slightly worse ranking, it produces confident false alarms about a family's
missing car. That happened:

  On 2026-09-12 the panel held 27 score-95 "verified" alerts, all identical,
  all reading the plate serial with a leading zero. Every one of them was
  preceded in the post by شاسي — a CHASSIS ending in the same digits as the
  plate serial, in one recovery list cross-posted to 26 Facebook groups. The
  plate rule allowed ANY single leading digit before the serial, so it matched
  ten times more numbers than the one spelling it was written for. Only the
  configured region digit glued to the serial is real.

Every case below is DERIVED from the `car:` block in config.yaml, so this file
carries no identifier of its own and the checks follow whatever vehicle a
deployment is configured for. Run it after touching `identifier_hit`,
`normalize.py`, or the `car:` block. It exits non-zero on the first failure.
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
from carhunt.prefilter import _region_digit, cap_unbacked_alert, identifier_hit

AR = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def ar(s: str) -> str:
    return s.translate(AR)


def build_cases(car: dict):
    vin = (car.get("vin") or "").strip()
    tail = vin[-7:]
    plate = car.get("plate") or ""
    serial = re.search(r"\d{4,6}", plate).group(0)
    region = _region_digit(plate)
    wrong = [d for d in "1234567890" if d != region][:3]
    # a number sharing the tail's first three digits but nothing else: the
    # 2026-10-08 "one-digit typo" resemblance case
    near = tail[:3] + ("0933" if tail[3:] != "0933" else "0944")
    unrelated = "8080808" if tail != "8080808" else "7070707"

    cases = [
        # --- must fire ---
        ("bare plate, ASCII",        f"لوحة {serial} خ{region}",           True),
        ("bare plate, Arabic-Indic", f"لوحة {ar(serial)}",                  True),
        ("glued region, ASCII",      f"لوحة {region}{serial}",              True),
        ("glued region, Arabic",     f"العربه خ{ar(region + serial)} مفقودة", True),
        ("full VIN",                 vin,                                   True),
        ("chassis tail",             f"شاسي {tail}",                        True),
        ("chassis tail, Arabic",     f"شاسي {ar(tail)}",                    True),

        # --- must NOT fire: the 2026-09-12 false-alarm family ---
        ("the 27 false positives",   f"امجاد شاسي {ar('0' + serial)} كريز", False),
        (f"wrong region digit {wrong[0]}", f"شاسي {wrong[0]}{serial}",      False),
        (f"wrong region digit {wrong[1]}", f"شاسي {wrong[1]}{serial}",      False),
        (f"wrong region digit {wrong[2]}", f"شاسي {wrong[2]}{serial}",      False),

        # --- must NOT fire: ordinary near-misses ---
        ("unrelated 6-digit",        "شاسي 987654",                         False),
        ("plate digits inside a longer run", f"شاسي 123{serial}12",         False),
        ("short fragment only",      f"شاسي {serial[-4:]}",                 False),
        ("empty",                    "",                                    False),
        ("no numbers",               "كورولا بيضاء للبيع",                   False),
    ]

    # The alert cap (2026-10-08): a model score of 95 survives only with an
    # identifier behind it. (label, verdict, post text, should it stay an alert?)
    cap_cases = [
        ("resemblance only, the 2026-10-08 case",
         {"score": 95, "extracted": {"vin": near}}, f"كورلا{ar(near)}", False),
        ("other owner's same model, different chassis",
         {"score": 75, "vehicles": [{"chassis": unrelated}]}, "كورولا بيضاء 2015", False),
        ("target chassis in the post text",
         {"score": 95}, f"شاسي {tail}", True),
        ("target chassis read off a photo",
         {"score": 95, "vehicles": [{"chassis": tail}]}, "", True),
        ("prose in the field never counts",
         {"score": 95, "extracted": {"vin": f"no match for {tail} here"}}, "", False),
        ("deterministic one-digit-off hit",
         {"score": 75, "identifier_hit": f"{tail[:-1]}0 is one character from {tail}"}, "", True),
    ]
    return cases, cap_cases


def main() -> None:
    car = load_config()["car"]
    region = _region_digit(car.get("plate") or "")
    print(f"target plate : {'set' if car.get('plate') else 'MISSING'}")
    print(f"region digit : {'parsed' if region else 'MISSING'}")
    print(f"target VIN   : {len((car.get('vin') or '').strip())} chars\n")
    if not region:
        print("FAIL: no region digit parsed from the configured plate — the "
              "glued spelling can no longer be matched at all.")
        sys.exit(1)
    if len((car.get("vin") or "").strip()) != 17:
        print("FAIL: car.vin must be the full 17-character VIN.")
        sys.exit(1)

    cases, cap_cases = build_cases(car)
    failures = 0
    for label, text, expected in cases:
        got = identifier_hit(text, car)
        ok = bool(got) == expected
        if not ok:
            failures += 1
        want = "fire" if expected else "stay silent"
        print(f"  {'ok  ' if ok else 'FAIL'}  {label:<36} (should {want})"
              f"{'' if ok else '  -> got ' + repr(got)}")

    print("\nalert cap:")
    for label, verdict, text, expected in cap_cases:
        v = dict(verdict)
        cap_unbacked_alert(v, text, car, 70)
        ok = (v["score"] >= 70) == expected
        if not ok:
            failures += 1
        want = "stay an alert" if expected else "be capped"
        print(f"  {'ok  ' if ok else 'FAIL'}  {label:<42} (should {want})"
              f"{'' if ok else '  -> score ' + str(v.get('score'))}")

    print()
    if failures:
        print(f"{failures} case(s) FAILED — do not ship this rule.")
        sys.exit(1)
    print(f"all {len(cases) + len(cap_cases)} cases pass")


if __name__ == "__main__":
    main()
