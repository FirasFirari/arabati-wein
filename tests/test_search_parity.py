"""The page's search and the standing-watch rule must give the same answer.

A visitor searches with the page's JavaScript; a registered watch is matched
by carhunt/watches.py. The audit of 2026-11-04 found five cases where they
disagreed: a family could be told "no match" by the page while the watch
matched, or the reverse. This runs the page's own queryCtx/dscoreAt (cut out
of export_site.TEMPLATE) in node and compares it with watches.match_reason.
Synthetic identifiers only.
"""
import json
import re
import shutil
import subprocess

import pytest

from carhunt.export_site import TEMPLATE
from carhunt.watches import match_reason, readings

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

# (label, query, stored chassis_norm, stored plate_norm, should match?)
CASES = [
    ("exact chassis", "613572", "613572", None, True),
    ("chassis suffix of a full VIN", "7654321", "XXXBB9HE5F7654321", None, True),
    ("6-digit fragment inside a stored chassis", "839215", "8392157", None, True),
    ("4-digit fragment mid-chassis is noise", "3921", "8392157", None, False),
    ("full VIN vs its 8-char make/model prefix", "JM0TEST4AGU100284", "JM0TEST4", None, False),
    ("full VIN vs a sister car", "JM0TEST4AGU100284", "JM0TEST4AGU100953", None, False),
    ("plate typed with region marker", "52731 خ 3", None, "52731", True),
    ("plate filed as a list row's chassis", "52731 خ 3", "52731", None, True),
    ("plate vs a passport-shaped line", "52731 خ 3", "P000000000XXX0000000M2527313", None, False),
    ("plate glued to its region marker", "خ٧٢٨٤٦١", None, "28461", True),
    ("plate serial that looks like a year", "2014 خ1", None, "2014", True),
    ("unrelated number", "613572", "123456", None, False),
    ("MRO misread finds the real MR0 VIN", "MROAW12GX00002178", "MR0AW12GX00002178", None, True),
]


def _cut(name: str) -> str:
    m = re.search(rf"^function {name}\(.*?^}}\n", TEMPLATE, re.S | re.M)
    assert m, name
    return m.group(0)


STUBS = """
const ALIASES = {};
const t = (k, a) => a;
"""

HARNESS = """
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const IX = {cn: [], pr: [], hay: [], mk: [], yr: []};
const out = cases.map(([q, cn, pn]) => {
  IX.cn[0] = normChassis(cn || ''); IX.pr[0] = pn ? ',' + pn + ',' : '';
  IX.hay[0] = ''; IX.mk[0] = ''; IX.yr[0] = '';
  const r = dscoreAt(0, queryCtx(q));
  return !!(r && r.reasons.some(x => x.includes('شاسي') || x.includes('لوحة')));
});
process.stdout.write(JSON.stringify(out));
"""


def page_matches(cases):
    start = TEMPLATE.index("const AR_DIGITS")
    helpers = TEMPLATE[start:TEMPLATE.index("const STATUS_AR")]
    src = STUBS + helpers + _cut("queryCtx") + _cut("dscoreAt") + HARNESS
    out = subprocess.run([NODE, "-e", src], input=json.dumps([c[1:4] for c in cases]),
                         capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


def watch_matches(case):
    _, query, cn, pn, _ = case
    qn, qp = readings(query)
    from carhunt.normalize import norm_chassis
    return bool(match_reason(qn, qp, norm_chassis(cn) if cn else cn, pn))


def test_page_and_watch_rule_agree_and_are_right():
    page = page_matches(CASES)
    wrong = []
    for case, p in zip(CASES, page):
        w, want = watch_matches(case), case[4]
        if not (p == w == want):
            wrong.append(f"{case[0]}: page={p} watch={w} want={want}")
    assert not wrong, "\n".join(wrong)


def test_search_cli_uses_the_same_rule():
    from carhunt.search import score_vehicle
    from carhunt.normalize import norm_chassis
    for label, query, cn, pn, want in CASES:
        v = {"chassis_norm": norm_chassis(cn) if cn else None, "plate_norm": pn}
        got = score_vehicle(query, v)[0] > 0
        assert got == want, label
