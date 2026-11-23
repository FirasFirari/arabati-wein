"""One car registered twice by one person is one watch (2026-11-20)."""
import json
import shutil
import subprocess

import pytest

from carhunt import watches as W
from carhunt.export_site import TEMPLATE

ROWS = [
    {"identifier": "173381", "contact": "0912345678", "note": "", "created_at": "2026-11-20T00:36:00Z"},
    {"identifier": "173381", "contact": "+249912345678", "note": "accent 2012 silver", "created_at": "2026-11-20T00:38:00Z"},
    {"identifier": "١٧٣٣٨١", "contact": "0912345678", "note": "accent 2012 silver", "created_at": "2026-11-21T09:00:00Z"},
    {"identifier": "173381", "contact": "0123456789", "note": "another person", "created_at": "2026-11-19T10:00:00Z"},
    {"identifier": "KMHJT81BBDU641476", "contact": "0912345678", "note": "", "created_at": "2026-11-18T10:00:00Z"},
    {"identifier": "641476", "contact": "0912345678", "note": "", "created_at": "2026-11-18T10:01:00Z"},
]


def test_same_number_same_person_is_one_watch():
    out = W.merge_repeats(ROWS)
    mine = [w for w in out if w["identifier"] == "173381" and w["contact"] == "0912345678"]
    assert len(mine) == 1
    w = mine[0]
    assert w["created_at"] == "2026-11-20T00:36:00Z"        # when the watch began
    assert w["note"] == "accent 2012 silver"               # the description added later
    assert w["repeats"] == 3 and w["updated_at"] == "2026-11-21T09:00:00Z"


def test_another_person_and_a_vin_fragment_stay_separate():
    out = W.merge_repeats(ROWS)
    assert len(out) == 4
    assert {w["identifier"] for w in out} == {"173381", "KMHJT81BBDU641476", "641476"}
    assert sum(1 for w in out if w["identifier"] == "173381") == 2


def test_different_notes_are_both_kept():
    rows = [dict(ROWS[0], note="silver"), dict(ROWS[1], note="accent 2012")]
    assert W.merge_repeats(rows)[0]["note"] == "silver / accent 2012"


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_page_remembers_a_registration_without_the_contact():
    """The page's own storage code, run in node with a fake localStorage."""
    helpers = TEMPLATE[TEMPLATE.index("const AR_DIGITS"):TEMPLATE.index("const ALIAS_EXACT")]
    store = TEMPLATE[TEMPLATE.index("// ---- registrations this device already made ----"):
                     TEMPLATE.index("// ---- end registrations ----")]
    src = ("const ALIASES = {}; const SKEL_STOP = [];\n"
           "const mem = {}; let broken = false;\n"
           "const localStorage = {getItem: k => { if (broken) throw new Error('blocked'); return mem[k] ?? null; },"
           " setItem: (k, v) => { if (broken) throw new Error('blocked'); mem[k] = v; }};\n"
           + helpers + store + """
const out = {};
out.before = findSavedWatch('173381');
rememberWatch('173381', false);
out.arabicDigits = !!findSavedWatch('\u0661\u0667\u0663\u0663\u0668\u0661');
out.other = findSavedWatch('173382');
out.noteBefore = findSavedWatch('173381').note;
rememberWatch('173381', true);
out.noteAfter = findSavedWatch('173381').note;
out.stored = mem.watches;
out.count = savedWatches().length;
mem.watches = 'not json';
out.garbage = savedWatches().length;
broken = true;
out.blockedRead = savedWatches().length;
out.blockedWrite = rememberWatch('555666', true).k;
process.stdout.write(JSON.stringify(out));
""")
    r = subprocess.run([NODE, "-e", src], capture_output=True, text=True, encoding="utf-8", check=True)
    out = json.loads(r.stdout)
    assert out["before"] is None and out["other"] is None
    assert out["arabicDigits"] is True                      # same number in Arabic digits
    assert out["noteBefore"] is False and out["noteAfter"] is True
    assert out["count"] == 1                                # an update is not a second entry
    stored = json.loads(out["stored"])
    assert set(stored[0]) == {"k", "at", "note"}            # never the contact
    assert out["garbage"] == 0 and out["blockedRead"] == 0  # broken storage: the page still works
    assert out["blockedWrite"] == "555666"
