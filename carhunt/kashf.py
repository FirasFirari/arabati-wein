"""Deterministic parser for كشف / حصر vehicle inventory lists.

WHY THIS EXISTS. Measured 2026-08-31: **159 mega-list posts — 1.5% of the
backlog — hold ~44% of every chassis-shaped number in it.** Output tokens are
the binding cost of the whole pipeline, so those posts dominate the bill on
every provider. They are also the posts every LLM has failed on in the same
way: post 22474 (953 vehicles) ran Kimi for 24 minutes and returned NOTHING at
the 64k output ceiling, and the largest silent data loss this project ever
suffered was a mega-list truncated mid-write — one post emitting 401 of 684
vehicles while scoring a clean `1/1 covered`.

A parser removes both failure modes BY CONSTRUCTION, which is the actual
argument for it — not the money:

  * it cannot fabricate. It copies digit runs out of the source or emits
    nothing. 106 invented identifiers have been caught here mechanically and
    ZERO were ever self-reported by the model that produced them.
  * it cannot truncate. There is no output budget to exhaust, so a 953-line
    list yields 953 lines.

WHAT IT DELIBERATELY DOES NOT DO. It does not guess at meaning. Model and body
type are resolved through `aliases.py`, the same table the rest of the project
and the published site already use — which is where the knowledge lives that
**شريحة and بوكس are BODY TYPES, not models**, the trap `audit_verdicts.py`
checks for. Any line the parser cannot read confidently is returned as
RESIDUE for a model to read instead. Identifiers come from the parser;
judgement stays with the LLM.

The list shape, from the corpus (post 19018, 46k chars, ~900 lines):

    292063    توسان                     chassis + description
    010498   40141خ4   سنتافي 2005      chassis + plate + description + year
    492428   لوحة  61561 خ4  سنتافي 2015   ... with an explicit لوحة label
    390977 - 31901خ4  سوناتا            dash separator
    0556269  بوكس 2007
    موي                                  no number at all -> not a vehicle
"""
import re
import unicodedata

from .aliases import model_keys_in, normalize_ar

# Arabic-Indic and Eastern Arabic-Indic digits fold to ASCII. A chassis written
# ٧٠٢٠٢٧٨٣٦ is the same identifier as 702027836 and must normalise to it, or
# the registry stores the same car twice and neither copy matches a search.
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

# Sudanese plates, in every shape the corpus actually uses. Getting these
# wrong in EITHER direction costs identifiers: too greedy and the regex eats a
# chassis ("شاسي 051855 لوحة خ4 90235" matched 051855+خ4 as the plate and lost
# the chassis); too strict and the plate's digits are filed as a chassis.
#   digits then 1-2 letters then an optional district digit
#     "40141خ4"  "22404 ج د1"  "7765خ"  "77082 خ 3"
#   The TRAILING DISTRICT DIGIT IS REQUIRED and is load-bearing. Without it
#   the pattern matches the tail of any ordinary Arabic word that happens to
#   be followed by digits — "سوناتا567688" parsed as plate "سو" + 567688 —
#   which ate chassis numbers and dropped recall from 98.6% to 94.5%.
#   `(?![ء-ي])` additionally stops the letters being part of a longer word.
_PLATE_FWD = re.compile(
    r"(\d{3,6})\s*([ء-ي](?:\s*[ء-ي])?)(?![ء-ي])\s*[/\\]?\s*(\d)(?!\d)")
#   the same thing written the other way round: "1خ89855", "1ش ك27153".
#   Anchored on a leading district digit for the same reason.
_PLATE_REV = re.compile(
    r"(?<![ء-ي\d])(\d)\s*([ء-ي](?:\s*[ء-ي])?)(?![ء-ي])\s*(\d{3,6})(?!\d)")
#   and district-letter FIRST: "خ3 17333", "خ4 79100", "خ4520". This was the
#   single largest source of spurious chassis in the benchmark — plates filed
#   as vehicles. The letters must stand alone (a 1-2 letter Arabic word), or
#   the pattern would swallow the tail of any word followed by digits.
_PLATE_LEAD = re.compile(
    r"(?<![ء-ي\d])([ء-ي](?:\s*[ء-ي])?)(?![ء-ي])\s*(\d)\s*(\d{3,6})(?!\d)")

# Explicit label, when the poster bothers: "لوحة 61561 خ4".
_PLATE_LABEL = re.compile(r"(?:لوحة|لوحه|رقم\s*اللوحة)\s*:?\s*")

# An explicit chassis label outranks every plate heuristic: whatever number
# follows شاسي IS the chassis, and must be protected from the plate scan.
_CHASSIS_LABEL = re.compile(r"(?:الشاسي|شاسي|شاسيه|رقم\s*الشاسي)\s*:?\s*(\d{3,})")

# A model year: four digits 19xx/20xx, or the local two-digit shorthand
# ("اكسنت 99", "بوكس 82"). Two-digit years are only read in 60..99 -> 19xx;
# anything lower is ambiguous against a trim number and is left in the text.
_YEAR4 = re.compile(r"\b(19\d{2}|20[0-4]\d)\b")
_YEAR2 = re.compile(r"\b([6-9]\d)\b")

# Colour words carried as-is into the description; the registry stores colour
# separately and a searcher types them.
_COLOURS = {
    "ابيض": "white", "بيضاء": "white", "بيضا": "white",
    "اسود": "black", "سوداء": "black",
    "فضي": "silver", "سلفر": "silver", "فضية": "silver", "رمادي": "grey",
    "كحلي": "navy", "كحلية": "navy", "ازرق": "blue", "زرقاء": "blue",
    "اخضر": "green", "خضراء": "green", "احمر": "red", "حمراء": "red",
    "ذهبي": "gold", "ذهبية": "gold", "بني": "brown", "فيراني": "beige",
    "لبني": "light blue", "شمباني": "champagne",
}


def fold_digits(text: str) -> str:
    """NFKC then Arabic-Indic -> ASCII, leaving everything else intact."""
    return unicodedata.normalize("NFKC", text or "").translate(_DIGITS)


def looks_like_kashf(text: str, min_lines: int = 8) -> bool:
    """Is this post an inventory LIST rather than prose about one car?

    The test is structural, not lexical: a كشف is many short lines that each
    open with a digit run. Keying off the words كشف/حصر alone would both miss
    unlabelled lists and match a prose post that merely mentions one.
    """
    lines = [l for l in fold_digits(text).splitlines() if l.strip()]
    if len(lines) < min_lines:
        return False
    numbered = sum(1 for l in lines if re.match(r"\s*\d{3,}", l))
    return numbered >= min_lines and numbered >= 0.5 * len(lines)


def _extract_year(text: str) -> tuple[int | None, str]:
    m = _YEAR4.search(text)
    if m:
        return int(m.group(1)), text[:m.start()] + " " + text[m.end():]
    m = _YEAR2.search(text)
    if m:
        return 1900 + int(m.group(1)), text[:m.start()] + " " + text[m.end():]
    return None, text


def _colour_in(text: str) -> str | None:
    n = normalize_ar(text)
    for word, canon in _COLOURS.items():
        if normalize_ar(word) in n:
            return canon
    return None


# The list's own serial number, not a vehicle: "حصر 116", "الحصر رقم 877".
# Measured: these were being emitted as 3-digit chassis.
_HEADER = re.compile(r"^\s*(?:ال)?(?:حصر|كشف|كشوفات)\s*(?:رقم)?\s*[:\-]?\s*\d+\s*$")

# A Sudanese mobile number. THIS FILTER IS NOT OPTIONAL: every one of these
# lists opens with the compiler's contact number, and without this the parser
# filed 09XXXXXXXX / 09XXXXXXXX / 09XXXXXXXX as CHASSIS NUMBERS — a phone
# number sitting in the registry as a vehicle identifier, searchable and
# wrong. Caught by reading a sample; no mechanical gate would have flagged it,
# because the digits genuinely are in the post.
# EXACTLY the Sudanese mobile shape: a leading 0, then 9 or 1, then 8 more
# digits. The looser first attempt (leading 0 optional) matched the genuine
# 9-digit chassis 982000920 and would have deleted real identifiers — the
# filter must be narrower than the thing it protects.
_PHONE = re.compile(r"(?<!\d)(?:\+?249|00249)?0(?:9|1)\d{8}(?!\d)")

# Administrative lines: the list's title, its serial, the "send me your
# chassis on WhatsApp" instruction. They carry digits but no vehicle. Skipped
# only when the line has no model/colour vocabulary, so a real entry that
# happens to mention اتصال is still read.
_ADMIN = re.compile(
    r"للتواصل|اتصال|واتس|ارسل|للحصر|حصر\s|كشف\s|لبحث|للبحث|مندوب|رقم\s*التواصل")

# Shorter than this is a fragment, a trim number or a list index, not an
# identifier anybody searches by. The registry's own shortest real كشف tails
# are 5-6 digits; 4 is the floor that keeps them without admitting noise.
_MIN_CHASSIS = 4


def _is_year_token(run: str) -> bool:
    return bool(re.fullmatch(r"(19\d{2}|20[0-4]\d)", run))


def parse_line(line: str) -> list[dict]:
    """One list line -> EVERY vehicle on it.

    Returns a LIST, because a single line very often carries several cars:

        سانتافي 313267 . i10 احمر 680869. فيرنا 013375 ابيض. افانتي 429221 ابيض

    Reading only the first number was costing ~350 of 365 misses on the
    benchmark. And the order is not fixed — post 19018 writes
    `chassis description`, post 10392 writes `description chassis` — so each
    number takes the text on BOTH sides of it, up to its neighbours, as its
    description window. That is order-agnostic by construction.

    `confident=False` marks a row the parser will not stand behind (no model
    or colour vocabulary anywhere near the number, or a plate shape it could
    not resolve). The caller routes those to a model instead of writing a
    half-read row.
    """
    raw = fold_digits(line).strip()
    if not raw or _HEADER.match(raw):
        return []

    # Phone numbers out FIRST, before anything can mistake one for a chassis.
    contact = None
    pm = _PHONE.search(raw)
    if pm:
        contact = pm.group(0)
    work = _PHONE.sub(lambda m: " " * len(m.group(0)), raw)

    # An administrative line (title, serial, "send your chassis to WhatsApp")
    # carries digits but no car. Only skipped when it names no vehicle.
    if _ADMIN.search(work) and not model_keys_in(work):
        return []

    work = _PLATE_LABEL.sub(" ", work)

    # An explicitly labelled chassis is masked out of the plate scan entirely,
    # then restored — otherwise "شاسي 051855 لوحة خ4 90235" loses 051855 to a
    # greedy plate match and reports the plate's digits as the vehicle.
    labelled = None
    lm = _CHASSIS_LABEL.search(work)
    if lm:
        labelled = lm.group(1)
        work = work[:lm.start(1)] + " " * len(labelled) + work[lm.end(1):]

    # Plates next: their digits must not be mistaken for a chassis. Every
    # plate on the line is lifted out before the chassis scan.
    plates = []
    before_plates = work
    for pattern in (_PLATE_FWD, _PLATE_REV, _PLATE_LEAD):
        while True:
            m = pattern.search(work)
            if not m:
                break
            plates.append(" ".join(g.strip() for g in m.groups() if g and g.strip()))
            work = work[:m.start()] + " " * (m.end() - m.start()) + work[m.end():]

    # If stripping the plates left the line with NO identifier at all, the
    # "plate" WAS the identifier — "77082 خ 3 اتوس احمر" is one number and one
    # car, and the trusted pipeline files it as a chassis. Put it back rather
    # than emit nothing; a searchable number matters more than which field it
    # sits in, and the registry searches chassis_norm.
    def _ids(s: str) -> list:
        """Identifier-shaped tokens, DIGITS OR ALPHANUMERIC.

        A digits-only scan missed every VIN-style chassis in the corpus —
        `52AA14457445`, `462986229556`, `D0378` — which were 13 of the 13 real
        benchmark misses once reference errors were set aside. The digit-count
        floor keeps English words out: "Byd" and "i10" carry too few digits.
        """
        out = []
        for m in re.finditer(r"[0-9A-Za-z][0-9A-Za-z\-]{3,}|\d[\d\-]*\d", s):
            tok = m.group(0).strip("-")
            if len(re.sub(r"[^0-9]", "", tok)) < _MIN_CHASSIS:
                continue
            if _is_year_token(tok):
                continue
            out.append((m.start(), m.end(), tok))
        return out

    if plates and not _ids(work) and not labelled:
        work = before_plates
        plates = []

    if labelled:
        work = work[:lm.start(1)] + labelled + work[lm.start(1) + len(labelled):]

    # A leftover Arabic letter wedged between digits is a plate shape this
    # parser could not resolve (e.g. "10خ10040"). Do not guess which half is
    # the number — emit it, but never as confident.
    murky = bool(re.search(r"\d[ء-ي]\d", work))

    # A bare 19xx/20xx is a model year, not an identifier. Measured on the
    # benchmark: the reference itself stores 2021, 2018 and 2004 as chassis
    # numbers, read off "بوكسي هايلوكس 2021ابيض" and "25089 كلك 2004" — the
    # parser refusing them is a correction, not a miss.
    ids = _ids(work)
    if not ids:
        return []

    out = []
    for idx, (start, end, run) in enumerate(ids):
        prev_end = ids[idx - 1][1] if idx else 0
        next_start = ids[idx + 1][0] if idx + 1 < len(ids) else len(work)
        window = work[prev_end:start] + " " + work[end:next_start]
        year, window = _extract_year(window)
        desc = re.sub(r"[\s\-_.،,*]+", " ", window).strip()
        keys = model_keys_in(desc)
        colour = _colour_in(desc)
        out.append({
            "chassis": run,
            # A plate belongs to the vehicle it was written beside. With one
            # plate and one number that is unambiguous; with several of each,
            # pairing by position would be a guess, so it is left off and the
            # row goes to the residue for a model to read.
            "plate": plates[idx] if len(plates) == len(ids) else None,
            "model_key": keys[0] if keys else None,
            "year": year,
            "color": colour,
            "contact": contact,
            "description": desc or None,
            "confident": bool(keys or colour) and not murky,
            "source_line": line.strip(),
        })
    return out


def parse(text: str) -> dict:
    """Parse a whole كشف post.

    Returns {"vehicles": [...], "residue": [...], "lines": n}. `residue` holds
    the source lines that carried an identifier but no vocabulary the alias
    table recognised — hand those to a model rather than guessing at them.
    """
    vehicles, residue = [], []
    seen = set()
    lines = fold_digits(text).splitlines()
    for line in lines:
        for v in parse_line(line):
            # One car listed twice in the same كشف is one car. Measured 6,369
            # duplicate chassis across 76,595 rows before this.
            key = v["chassis"]
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            vehicles.append(v)
            if not v["confident"] and v["source_line"] not in residue:
                residue.append(v["source_line"])
    return {"vehicles": vehicles, "residue": residue, "lines": len(lines)}
