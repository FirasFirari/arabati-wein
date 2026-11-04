"""Python <-> JavaScript parity of the normalisation helpers.

The published page searches with JavaScript; the index it searches was built
with Python. If the two normalise a number differently, a family types their
chassis and the page cannot find the entry built for it -- with no error
anywhere. The rule "change one, change both" was enforced only by comments;
this test runs the page's own JS (cut out of export_site.TEMPLATE) in node and
diffs it against carhunt/normalize.py and carhunt/aliases.py.
"""
import json
import shutil
import subprocess

import pytest

from carhunt.aliases import normalize_ar
from carhunt.export_site import TEMPLATE
from carhunt.normalize import ARABIC_INDIC, digits_only, is_plate_shaped, norm_chassis

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def _js_helpers() -> str:
    start = TEMPLATE.index("const AR_DIGITS")
    end = TEMPLATE.index("const ALIAS_EXACT")
    return TEMPLATE[start:end]


RUNNER = """
const inputs = JSON.parse(require('fs').readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(inputs.map(s =>
  [trAr(s), digitsOnly(s), normChassis(s), isPlateShaped(s), normAr(s)])));
"""


def js(values):
    out = subprocess.run([NODE, "-e", _js_helpers() + RUNNER], input=json.dumps(values),
                         capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


def py(value):
    return [(value or "").translate(ARABIC_INDIC), digits_only(value), norm_chassis(value),
            is_plate_shaped(value), normalize_ar(value)]


# Synthetic only. Each line exercises one way an identifier reaches the page.
CORPUS = [
    "", "7614337", "٧٦١٤٣٣٧", "۷۶۱۴۳۳۷", "jtd br32e-72 001", "JTTEST01J3L410078",
    "28461 خ 7", "28461 / خ7", "خ٧٢٨٤٦١", "70352خ6", "1خ63922", "خ3 28444",
    "لوحة 28461", "315 / ج ك 2", "شاسي: AB-12/34", "كورولا 2015 ابيض",
    "سانتافي 421376 . i10 احمر", "أإآ ة ى ؤ ئ", "كـورولا", "مُفَقْودة", "ABC123\tخ5",
    "0912 345 678", "+249 912 345 678", "رقم الشاسي ٥١١-٣٣٣٥",
]


def test_identifier_helpers_match():
    """trAr / digitsOnly / normChassis / isPlateShaped: 0 differences."""
    got = js(CORPUS)
    diffs = []
    for value, j in zip(CORPUS, got):
        p = py(value)
        for name, a, b in zip(("trAr", "digitsOnly", "normChassis", "isPlateShaped"), j, p):
            if a != b:
                diffs.append((name, value, a, b))
    assert not diffs


def test_arabic_text_folding_matches_without_digits():
    no_digits = [v for v in CORPUS if not any(ch.isdigit() for ch in v)]
    got = js(no_digits)
    assert [g[4] for g in got] == [normalize_ar(v) for v in no_digits]


@pytest.mark.xfail(strict=True, reason="audit 2026-11-04 (L2): JS normAr strips U+0660-0669, "
                                       "deleting Arabic-Indic digits that Python keeps")
def test_arabic_text_folding_keeps_arabic_indic_digits():
    value = "كورولا ٢٠١٥"
    assert js([value])[0][4] == normalize_ar(value)


@pytest.mark.private
def test_parity_on_real_registry_values(ro_db):
    """Same diff over a sample of what is actually stored (values never printed)."""
    rows = ro_db.execute(
        "SELECT chassis, plate FROM vehicles WHERE rowid % 97 = 0 LIMIT 20000").fetchall()
    values = sorted({v for r in rows for v in r if v})
    got = js(values)
    bad = sum(1 for v, j in zip(values, got) if j[:4] != py(v)[:4])
    assert bad == 0, f"{bad} of {len(values)} stored values normalise differently in JS"
