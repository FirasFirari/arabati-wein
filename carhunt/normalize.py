"""Normalize vehicle identifiers so Arabic/Latin digits, spacing, and
partial numbers all match. Used by both the search CLI and the exported
search page (the JS mirrors these rules)."""
import re

ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def digits_only(value: str) -> str:
    """Keep only digits, converting Arabic-Indic numerals to ASCII."""
    return re.sub(r"\D", "", (value or "").translate(ARABIC_INDIC))


def norm_chassis(value: str) -> str:
    """Uppercase alphanumerics only (VINs and partial chassis fragments)."""
    value = (value or "").translate(ARABIC_INDIC)
    return re.sub(r"[^A-Z0-9]", "", value.upper())


# an Arabic region letter glued straight onto the digits, no separator:
# "خ١١٢٣٤٥" is the plate "12345 خ1" written without the space.
_GLUED_REGION = re.compile(r"[ء-ي]\s*([0-9])([0-9]{5,})")


def _split_glued(value: str) -> str | None:
    """Serial from a region-letter-glued plate, or None if not that shape.

    Only splits when at least 5 digits follow the region digit. A shorter run
    ("خ18464") is genuinely ambiguous — region digit 1 + serial 8464, or region
    خ with no digit + serial 18464 — and guessing there would corrupt the more
    common reading, so it is left alone.
    """
    m = _GLUED_REGION.search(value)
    return m.group(2) if m else None


def norm_plate(value: str) -> str:
    """Plate → its SERIAL digits only.

    Sudanese plates read like "12345 خ1": a serial plus a region marker whose
    letter carries its own digit. Flattening every digit gives "123451", so a
    person searching the serial they actually remember ("12345") would miss.
    Take the longest digit run instead — the serial is the long one.

    When the region marker is written glued to the serial with no separator
    ("خ١١٢٣٤٥"), there is only ONE run and the longest-run rule silently
    returns "112345" — the region digit absorbed into the serial, which is
    exactly the miss this function exists to prevent. Peel it off first.
    """
    value = (value or "").translate(ARABIC_INDIC)
    runs = re.findall(r"[0-9٠-٩۰-۹]+", value)
    if not runs:
        return ""
    longest = max(runs, key=len)
    glued = _split_glued(value)
    # only prefer the split when it explains THIS run — i.e. the run we would
    # otherwise return is the region digit plus that serial
    if glued and len(glued) == len(longest) - 1 and longest.endswith(glued):
        return glued
    return longest


def plate_variants(value: str) -> set[str]:
    """Every digit run in a plate, so either half can be matched.

    Includes both readings of a glued region marker, since the set is used for
    lookup where an extra candidate costs nothing but a miss costs everything.
    """
    value = (value or "").translate(ARABIC_INDIC)
    runs = re.findall(r"[0-9٠-٩۰-۹]+", value)
    out = {r for r in runs if len(r) >= 3}
    glued = _split_glued(value)
    if glued and len(glued) >= 3:
        out.add(glued)
    return out


# How a PERSON types a plate into the search box or the watch form: a serial
# plus a region marker ("41019 خ 9", "31305 خ1", "خ1 12345", "218 / ج ك 9"),
# the marker glued on ("خ١١٢٣٤٥", "40141خ4"), or an explicit لوحة. A marker is
# one Arabic letter, or two only when a digit is attached — two-letter words
# (او، من، في) are common in typed text and are not plates. و alone is "and".
_PLATE_WORD = re.compile(r"^(?:ال)?لوح[ةه]$")
_MARKER_TOKEN = re.compile(
    r"^(?:[ء-نهي][0-9]?|[0-9][ء-نهي]|[ء-ي]{2}[0-9]|[0-9][ء-ي]{2})$")
_GLUED_TOKEN = re.compile(r"^(?:[ء-ي]{1,2}[0-9]{4,}|[0-9]{3,}[ء-ي]{1,2}[0-9]?)$")


def is_plate_shaped(value: str) -> bool:
    """Is this typed identifier a PLATE, so it has no chassis reading at all?

    `norm_chassis` strips the Arabic letter and glues the runs together:
    "41019 خ 9" becomes "410199", a number written nowhere. Matched as a
    chassis suffix it found a missing man's passport, whose machine-readable
    line ends in ...M2410199 (2026-10-08, two false watch leads). A chassis is
    never written with a region marker, so a plate-shaped value is matched by
    its digit runs only (`plate_variants`).
    """
    v = (value or "").translate(ARABIC_INDIC)
    if not re.search(r"[0-9]{3,}", v):
        return False
    for tok in re.split(r"[\s/\\_,،.:|-]+", v):
        if (_PLATE_WORD.match(tok) or _MARKER_TOKEN.match(tok)
                or _GLUED_TOKEN.match(tok)):
            return True
    return False


def chassis_overlap(query: str, chassis: str) -> int:
    """Length of the identifier overlap between a query and a stored chassis.

    People write chassis tails with varying lengths (last 5-9 chars), so a
    match means one string ends with the other (or contains it, for mid-VIN
    fragments). Returns the overlap length, 0 if no meaningful match.
    """
    q, c = norm_chassis(query), norm_chassis(chassis)
    if len(q) < 4 or len(c) < 4:
        return 0
    shorter, longer = (q, c) if len(q) <= len(c) else (c, q)
    if longer.endswith(shorter) or shorter in longer:
        return len(shorter)
    # allow the query to carry MORE of the VIN than the stored fragment:
    # e.g. query 1122334 vs stored 122334 handled above; a full 17-char VIN
    # vs stored 1122334 also handled by `in`. Nothing more needed.
    return 0


# --- commentary guard -------------------------------------------------------
# Agents write explanations into identifier fields. Because norm_* strips
# non-alphanumerics, that commentary is folded into the value: one row with
#   chassis = "5223949 (from text; does not match target VIN fragment 1122334)"
# normalised to ...FRAGMENT1122334 and registered as a match on 1122334, the
# exact number this project exists to find. The mirror failure is worse - a
# real match with prose appended is invisible to an exact-match query.
_BRACKETED = re.compile(r"[(\[][^)\]]*[)\]]")
_DIGIT = re.compile(r"[0-9\u0660-\u0669\u06F0-\u06F9]")
_LETTERS = re.compile(r"[A-Za-z\u0621-\u064A]")
_TOKEN = re.compile(r"[^\s/,;]+")


def _is_prose_token(tok: str) -> bool:
    """A word, as opposed to an identifier or a plate letter.

    A token holding a digit is NEVER prose, even when it starts with letters:
    an earlier version stripped the `RKLB` off `RKLB...4638126` and destroyed
    the Kuozui prefix that identifies the target's own plant. Tokens of one or
    two letters are kept too - Sudanese plates carry letter prefixes such as
    the target's own خ.
    """
    if _DIGIT.search(tok):
        return False
    return len(_LETTERS.findall(tok)) >= 3


def has_prose(value) -> bool:
    """True only if the field carries words, not merely several numbers.

    `637678 / 923901103916` is two real identifiers side by side; truncating it
    would delete the second. Only fields with actual words are rewritten.
    """
    v = str(value or "")
    if _BRACKETED.search(v):
        return True
    return any(_is_prose_token(t) for t in _TOKEN.findall(v))


def strip_prose(value):
    """Drop commentary words, keep every identifier token intact."""
    v = _BRACKETED.sub(" ", str(value or ""))
    seen, out = set(), []
    for t in _TOKEN.findall(v):
        if _is_prose_token(t):
            continue
        t = t.strip(".,;:-،–—")
        if not (_DIGIT.search(t) or _LETTERS.search(t)):
            continue      # a bare dash is not an identifier
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return " / ".join(out) if out else None
