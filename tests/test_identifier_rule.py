"""The deterministic target scan and the alert cap.

`identifier_hit` is the only check that may declare a match on its own
authority, so a loose rule produces confident false alarms about a missing car
(27 of them on 2026-09-12). These cases are the ones in
scripts/check_identifier_rule.py, derived from a car profile: the fictional one
in public, the configured one privately.
"""
import pytest
import yaml

import check_identifier_rule as rule
from carhunt.prefilter import _region_digit, cap_unbacked_alert, identifier_hit
from conftest import FIXTURES

CAR = yaml.safe_load((FIXTURES / "config.test.yaml").read_text(encoding="utf-8"))["car"]
CASES, CAP_CASES = rule.build_cases(CAR)


def test_fixture_car_is_well_formed():
    assert len(CAR["vin"]) == 17
    assert _region_digit(CAR["plate"]), "glued-plate matching needs a region digit"


@pytest.mark.parametrize("label, text, expected", CASES, ids=[c[0] for c in CASES])
def test_identifier_hit(label, text, expected):
    got = identifier_hit(text, CAR)
    assert bool(got) == expected, f"got {got!r}"


@pytest.mark.parametrize("label, verdict, text, expected", CAP_CASES,
                         ids=[c[0] for c in CAP_CASES])
def test_alert_cap(label, verdict, text, expected):
    v = dict(verdict)
    cap_unbacked_alert(v, text, CAR, 70)
    assert (v["score"] >= 70) == expected, f"score {v.get('score')}"


def test_capped_score_is_held_below_the_threshold():
    v = {"score": 95, "extracted": {"vin": "4441111"}}
    cap_unbacked_alert(v, "كورولا بيضاء", CAR, 70)
    assert v["score"] == 69


@pytest.mark.private
def test_identifier_rule_on_configured_car(real_config):
    car = real_config["car"]
    threshold = real_config["matching"]["alert_threshold"]
    cases, cap_cases = rule.build_cases(car)
    bad = [label for label, text, want in cases if bool(identifier_hit(text, car)) != want]
    for label, verdict, text, want in cap_cases:
        v = dict(verdict)
        cap_unbacked_alert(v, text, car, threshold)
        if (v["score"] >= threshold) != want:
            bad.append(label)
    assert not bad
