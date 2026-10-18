"""Zero-cost gates that run BEFORE any API call.

Two jobs:
1. `is_relevant()` — cheap check for whether a post could possibly concern a
   vehicle. Posts with no vehicle vocabulary, no chassis-like number and no
   images cannot be about the car, so they never reach the model.
2. `identifier_hit()` — deterministic scan for OUR car's identifiers. Free,
   exact, and independent of the model; a hit is an immediate alert.
"""
import re

from .aliases import ALIASES, normalize_ar
from .normalize import digits_only, has_prose, norm_chassis

# generic vehicle vocabulary (Arabic + English), beyond the model aliases
_GENERIC = [
    "شاسي", "شاسيه", "الشاسي", "عربة", "عربه", "عربية", "عربيه", "عربات",
    "سيارة", "سياره", "سيارات", "لوحة", "لوحه", "لوح", "موديل", "مركبة",
    "مركبه", "مركبات", "للبيع", "مفقود", "مفقوده", "مفقودة", "مسروق",
    "مسروقة", "منهوب", "منهوبة", "كشف", "حصر", "موجودة", "موجوده", "بوكس",
    "دفار", "ركشة", "ركشه", "تكتك", "امجاد", "أمجاد", "حوش", "المرور",
    "chassis", "vin", "plate", "car", "vehicle", "toyota", "hyundai", "kia",
    "nissan", "mitsubishi", "corolla",
]

_VOCAB = set()
for _key, _names in ALIASES.items():
    for _n in _names + [_key]:
        _VOCAB.add(normalize_ar(_n))
for _g in _GENERIC:
    _VOCAB.add(normalize_ar(_g))

# 5+ consecutive digits looks like a chassis fragment / plate / list entry
_LONG_NUMBER = re.compile(r"[0-9٠-٩۰-۹]{5,}")

# "خ5" / "5خ" — a Sudanese region marker: one Arabic letter carrying one digit.
_REGION_MARKER = re.compile(r"([0-9٠-٩۰-۹])\s*[ء-ي]|[ء-ي]\s*([0-9٠-٩۰-۹])")


def _region_digit(plate: str) -> str:
    """The digit of the plate's region marker, e.g. "5" from "12345 / خ1".

    Returns "" when the plate carries no region marker, which callers must
    treat as "cannot match the glued spelling" rather than "match anything".
    """
    for match in _REGION_MARKER.finditer(plate or ""):
        digit = match.group(1) or match.group(2)
        if digit:
            return digits_only(digit)
    return ""


def has_vehicle_words(text: str | None) -> bool:
    if not text:
        return False
    norm = normalize_ar(text)
    if any(term in norm for term in _VOCAB):
        return True
    return bool(_LONG_NUMBER.search(text))


def is_relevant(text: str | None, image_paths: list[str] | None,
                strict: bool = False) -> bool:
    """True if the post is worth spending an API call on.

    `strict` is for general-interest sources (news channels), where nearly
    every post carries a photo and images alone would wave everything
    through. There, the text must also mention a vehicle — a post showing
    abandoned cars says so in words.
    """
    if image_paths and not strict:
        return True  # an image could be a car or a photographed list
    if image_paths and has_vehicle_words(text):
        return True
    if image_paths:
        return False
    return has_vehicle_words(text)


def identifier_hit(text: str | None, car: dict) -> str | None:
    """Deterministic scan for the target car's own identifiers.

    Matches against individual tokens, never a flattened blob: these posts are
    lists of numbers, and concatenating across entries invents sequences that
    are not in the text (e.g. "…641" + "خ1/ 33352" would fake "113335").
    """
    if not text:
        return None
    chassis = norm_chassis(car.get("vin") or "")
    tails = {chassis} if chassis else set()
    for n in (6, 7, 8):
        if len(chassis) > n:
            tails.add(chassis[-n:])
    tails = {t for t in tails if len(t) >= 6}

    # tokens = runs of alphanumerics as they actually appear in the post
    tokens = [norm_chassis(t) for t in re.findall(r"[0-9A-Za-z٠-٩۰-۹]+", text)]
    for token in tokens:
        if len(token) < 6:
            continue
        for tail in tails:
            # a listed fragment is the tail of the full VIN, or vice versa
            if token == tail or token.endswith(tail) or tail.endswith(token):
                return f"chassis {token} matches target tail {tail}"

    # the config plate reads like "12345 / خ1 (Khartoum private)"; take the
    # first long digit run so the region letter's digit isn't absorbed
    plate_runs = _LONG_NUMBER.findall(car.get("plate") or "")
    plate_digits = digits_only(plate_runs[0]) if plate_runs else ""
    region_digit = _region_digit(car.get("plate") or "")
    if len(plate_digits) >= 4:
        for token in tokens:
            token_digits = digits_only(token)
            if token_digits == plate_digits:
                return f"plate {plate_digits}"
            # A region marker written glued to the serial ("خ١١٢٣٤٥" for
            # "12345 خ1") tokenizes as ONE run, because the tokenizer keeps
            # only alphanumerics and the Arabic letter is not one — leaving
            # the region digit stuck on the front.
            #
            # It must be THIS car's region digit. Accepting any leading digit
            # (what this did until 2026-09-12) multiplies the target by ten and
            # matches unrelated numbers: it raised 27 score-95 "verified"
            # alerts on `٠١٢٣٤٥`, which every post labelled شاسي — an Amjad's
            # CHASSIS ending in the plate serial, not a plate, in one recovery list
            # cross-posted to 26 groups. With no region digit in the config
            # there is nothing to anchor to, so the glued form is not matched
            # at all rather than guessed.
            if (region_digit
                    and token_digits == region_digit + plate_digits):
                return (f"plate {plate_digits} (region marker glued to the "
                        f"serial as {token_digits})")
    return None


def alert_backing(verdict: dict, text: str | None, car: dict) -> str | None:
    """The deterministic evidence behind a high score, or None if there is none.

    Counts: a hit a lane already recorded on the verdict (`identifier_hit`: in
    the text, read off a photo, or one digit off), the `identifier_scan` tier,
    the target's identifiers in the post text or its photo OCR, or an
    identifier the verdict itself extracted that matches the target. Prose in
    an identifier field is skipped: models write "no <target tail> here" there.
    """
    if verdict.get("identifier_hit"):
        return str(verdict["identifier_hit"])
    if verdict.get("tier") == "identifier_scan":
        return "identifier_scan"
    hit = identifier_hit(text, car)
    if hit:
        return hit
    values = []
    for v in verdict.get("vehicles") or []:
        if isinstance(v, dict):
            values += [v.get("chassis"), v.get("plate")]
    extracted = verdict.get("extracted")
    if isinstance(extracted, dict):
        values += [extracted.get("vin"), extracted.get("plate")]
    for value in values:
        if value and not has_prose(str(value)):
            hit = identifier_hit(str(value), car)
            if hit:
                return hit
    return None


def cap_unbacked_alert(verdict: dict, text: str | None, car: dict,
                       threshold: int) -> bool:
    """Hold a model's score below the alert line unless an identifier backs it.

    A model's score is an opinion about resemblance, and white Corollas are
    common. Gemini Flash-Lite scored six posts 95 that way (2026-09-05 and
    2026-10-08), once calling a number "a one-digit typo" of the target tail when three
    digits differ, and inventing "white 2015" for a comment that gave neither.
    Sonnet put two other owners' lost Corollas, with different chassis, at
    70-75. None was this car. So a claim with no identifier behind it may
    still be a CANDIDATE (threshold - 1), never an alert, and the model's own
    number is kept as `model_score`. Returns True if it capped.
    """
    score = verdict.get("score")
    if not isinstance(score, (int, float)) or score < threshold:
        return False
    if alert_backing(verdict, text, car):
        return False
    verdict["model_score"] = score
    verdict["score"] = threshold - 1
    verdict["alert_capped"] = (
        "no identifier of the target car in the post, its photo OCR or the "
        "verdict's own readings; resemblance alone makes a candidate, never "
        "an alert")
    return True
