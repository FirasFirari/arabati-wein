"""كشف parser recall against a frozen answer key it did not write.

Built by tests/tools/build_kashf_gold.py from list posts whose rows came from
other lanes. Measured 2026-11-04: 99.30% of the 64,624 text-grounded truth
chassis across 750 list posts. The floor below lets a change cost at most
~30 chassis before it fails; a parser change that moves recall down further is
a regression whatever else it improves.
"""
import json

import pytest

from carhunt import kashf
from carhunt.config import DATA_DIR
from carhunt.normalize import norm_chassis

GOLD = DATA_DIR / "gold" / "kashf_gold.json"
RECALL_FLOOR = 0.9925


@pytest.mark.private
def test_kashf_recall_on_frozen_gold():
    if not GOLD.exists():
        pytest.skip("run tests/tools/build_kashf_gold.py first")
    posts = json.loads(GOLD.read_text(encoding="utf-8"))["posts"]
    total = found = 0
    for p in posts:
        got = {norm_chassis(v["chassis"]) for v in kashf.parse(p["text"])["vehicles"]}
        src = norm_chassis(kashf.fold_digits(p["text"]))
        truth = {t for t in p["truth"] if t in src}   # only what the text can yield
        total += len(truth)
        found += len(truth & got)
    assert total > 60000, "gold set shrank; rebuild it deliberately, never silently"
    assert found / total >= RECALL_FLOOR, f"recall {found / total:.3%} < {RECALL_FLOOR:.2%}"
