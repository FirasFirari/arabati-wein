"""The grounding gate, in ONE place, applied to EVERY write path.

An identifier (chassis, plate, contact, price) whose digits are not in the
post's own text or its photo OCR was not read -- it was invented. 106 invented
identifiers were caught this way; not one was self-reported by the model.

Until 2026-11 the gate lived in scripts/ingest_local_verdicts.py and ran only
on the lanes that went through it. The live Telegram lane, the batch lane and
the Gemini lane on photo posts without OCR wrote identifiers nobody checked
(measured: 8.6% of chassis and 27% of plates from one photo lane were not in
the post or its OCR). db.add_vehicles now calls `check_entries` for every
lane.

Two refinements over the old gate:

* WHOLE NUMBERS. The old check looked for a value's digits inside one string
  of every digit in the post joined together. On a 20,000-character list that
  accepted 52% of invented 4-digit values: they are substrings of the joined
  stream somewhere. A value must now sit inside ONE number of the source (a
  number written with spaces or dashes inside it counts as one number).
* PHOTOS NOT YET READ. A value read off a photo that has no OCR text yet
  cannot be checked either way. It is kept -- it may be the only record of a
  car -- but marked low_confidence, so the published row says so.
"""
from __future__ import annotations

import re
import unicodedata

from .normalize import has_prose, strip_prose

GROUNDED_FIELDS = ("chassis", "plate", "contact", "price")
MIN_RUN = 3          # shorter digit runs are structure (a region digit, "15"), not identity
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
UNCHECKED_PHOTO = "read from a photo not yet OCR-checked"


def _fold(text) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).translate(_DIGITS)


def source_runs(text: str) -> set[str]:
    """Every number in the source, as written and with inner separators removed."""
    t = _fold(text)
    runs = set(re.findall(r"\d+", t))
    runs |= {re.sub(r"\D", "", m) for m in re.findall(r"\d+(?:[ \-./]\d+)+", t)}
    return runs


def grounded(value, runs: set[str]) -> bool:
    """Is every identifier-shaped digit run of `value` inside ONE source number?"""
    v = _fold(value)
    parts = [r for r in re.findall(r"\d+", v) if len(r) >= MIN_RUN]
    if not parts:
        # only short groups, e.g. a plate "12خ3" split by its region letter:
        # each group must still be in the source, but on its own
        parts = re.findall(r"\d+", v)
    return all(any(p in r for r in runs) for p in parts)


def check_entries(entries: list[dict], text: str, photos_unread: bool) -> list[str]:
    """Gate `entries` IN PLACE against `text` (post text + photo OCR).

    Ungrounded values are nulled -- unless the post has photos that have not
    been OCR'd yet, in which case nothing can be checked and the row is kept
    with a low_confidence reason instead. Prose in an identifier field is
    stripped. Returns a description of every change, for the caller's log.
    """
    runs = source_runs(text)
    changes = []
    for e in entries:
        for f in ("chassis", "plate"):
            if e.get(f) and has_prose(e[f]):
                e[f] = strip_prose(e[f]) or None
                changes.append(f"{f}: prose stripped")
        unchecked = False
        for f in GROUNDED_FIELDS:
            val = e.get(f)
            if not val or grounded(val, runs):
                continue
            if photos_unread:
                unchecked = True
                continue
            changes.append(f"{f}={val!r} not in the post or its OCR: nulled")
            e[f] = None
        if unchecked and not e.get("low_confidence"):
            e["low_confidence"] = UNCHECKED_PHOTO
    return changes
