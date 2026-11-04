"""Standing-watch matching: the one rule shared by check_watches, the dashboard
and the hourly raw sweep. Cases live in scripts/check_watch_rule.py."""
import json

import pytest

import check_watch_rule as rule
from carhunt.config import DATA_DIR


def test_watch_rule_fictional_car(car):
    assert rule.run(car) == []


@pytest.mark.private
def test_watch_rule_configured_car(real_config):
    assert rule.run(real_config["car"]) == []


@pytest.mark.private
def test_human_reviewed_judgements_still_load():
    """data/watch_reviewed.json holds ~360 human verdicts ("different car",
    "own post"). They suppress leads already judged; losing the file would
    re-raise every one of them to the operator."""
    from carhunt.watches import load_reviewed
    raw = json.loads((DATA_DIR / "watch_reviewed.json").read_text(encoding="utf-8"))
    reviewed = load_reviewed()
    assert len(raw) >= 300
    assert len(reviewed) >= 300
