"""Deterministic reader for ORDINARY text posts (not the big كشف lists).

MEASUREMENT ONLY for now: nothing calls this except
scripts/prose_benchmark.py, and it writes nothing.

The list parser (kashf.py) proved that copying numbers out of the source beats
any model on recall and cannot fabricate. This applies the same idea to prose
posts ("موجود بوكس 2010 الشاسي 0039141 ..."), with one difference that
matters: in a list every number is a vehicle; in prose most numbers are NOT
(prices, mileage, engine size, phones, dates). So every number of the post
must end up in exactly one of three piles:

  identifier   a labelled chassis or plate, a VIN, or a number on a list line
  not one      a phone, a year, a price, a mileage, a date
  unexplained  anything else

One unexplained number and the post is NOT settled: a model reads it. The
reader never guesses, which is the whole point of it.

Each vehicle carries the `rule` that produced its identifier, so the benchmark
can grade the rules one by one.
"""
from __future__ import annotations

import re

from .aliases import model_keys_in
from .kashf import _PHONE, _colour_in, fold_digits, parse_line

__all__ = ["extract", "fold_digits", "clean"]

AR = "ء-ي"
_INVISIBLE = re.compile("[​-‏‪-‮⁦-⁩﻿ـ]")


def clean(text: str) -> str:
    """Digits folded, invisible marks and tatweel removed, ى read as ي.

    "رقم الشاسي<LRM>20702329690" and "الشاسى 0039141" were the two commonest
    misses of the first run: a label the eye reads and a regex does not.
    """
    # "#شاسي188783": the hashtag sign glues a label to nothing a regex reads
    t = _INVISIBLE.sub("", fold_digits(text or "")).replace("ى", "ي").replace("#", " ")
    # "702329690--702329690": one number written twice, not one long number
    return re.sub(r"-{2,}", " - ", t)


# --- the "not an identifier" pile -------------------------------------------
_PHONES = [
    re.compile(r"(?<![\dA-Za-z])\+\d[\d \-]{8,16}\d"),
    # 00-prefixed international numbers only when written in one piece:
    # "000472 - 000472" is a chassis written twice, not a phone
    # ...and only behind a real country code: "0051904040335" is a chassis
    re.compile(r"(?<![\dA-Za-z])00(?:249|966|971|974|968|965|973|20|218|211|251|254|256|235|90|44)"
               r"\d{7,10}(?!\d)"),
    re.compile(r"(?<![\dA-Za-z])(?:\+?249|00249)?0(?:9|1)\d{8}(?!\d)"),
    re.compile(r"(?<![\dA-Za-z])0(?:9|1)\d(?:[ .\-]?\d){7}(?!\d)"),
    re.compile(r"(?<![\dA-Za-z])249(?:9|1)\d{8}(?!\d)"),
    # a local number typed with one digit too many: "09123456789"
    re.compile(r"(?<![\dA-Za-z])0(?:9|1)\d{9}(?!\d)"),
]
_CONTACT_LABEL = re.compile(
    r"(?:تواصل|اتصال|[اأإ]تصال|و[اأ]تس\w*|تلفون\w*|تليفون\w*|موبايل|هاتف|جوال|للاستفسار"
    r"|whatsapp|call|tel|phone|mobile)[^\d\n" + AR + r"]{0,6}"
    r"(\d[\d \-.]{5,}\d)", re.I)
_DATE = re.compile(r"(?<!\d)\d{1,4}[/.\-]\d{1,2}[/.\-]\d{2,4}(?!\d)|(?<!\d)\d{1,2}:\d{2}(?!\d)")
_GROUPED = re.compile(r"(?<!\d)\d{1,3}(?:,\d{3})+(?!\d)")
_MEASURE_BEFORE = re.compile(
    r"(?:سعر\w*|السعر|بسعر|مبلغ|المبلغ|بشاره|بشارة|مكافا[هة]|مكافأ[هة]|عداد|العداد"
    r"|ماشي[هة]?|ممشي|كاش|مقدم|قسط|price)"
    r"\s*(?:رقم)?\s*[:\-(]?\s*(\d[\d.]*)", re.I)
_MEASURE_AFTER = re.compile(
    r"(\d[\d.]*)\s*(?:الف|ألف|مليون|مليار|جنيه|دولار|\$|درهم|ريال|كلم|كيلو|km|cc"
    r"|سي\s*سي|حصان|متر|%)", re.I)
# Counts, list serials and engine sizes: SHORT numbers only. "اكثر من 3000
# سيارة" is a count, but "خ 609391 سيارة موجودة" is a plate and "اي تن ماكينة
# 732187" an engine number: the first version of this rule ate both.
_SMALL_BEFORE = re.compile(
    r"(?:مكن[هة]|مكين[هة]|ماكين[هة]|موتور|مجموع[هة]|حصر|كشف|عدد|اكثر من|اكتر من)"
    r"\s*(?:رقم)?\s*[:\-(]?\s*(\d{1,4})(?!\d)")
_SMALL_AFTER = re.compile(r"(?<!\d)(\d{1,4})\s*(?:عربي[هة]|عرب[هة]|سيار[هة]|مركب[هة])")
_YEAR = re.compile(r"(?<![\dA-Za-z])(19[5-9]\d|20[0-3]\d)(?![\dA-Za-z])")

# --- the identifier pile -----------------------------------------------------
_FILL = r"(?:\s|رقم(?:ه|ها)?|نمر[هة]|بالرقم|هو|no\.?|number|[:\-=/#.(،*])*"
_ID = r"([0-9A-Za-z]+(?:[\-_][0-9A-Za-z]+)*)"
_CHASSIS_WORD = (r"(?:ال)?(?:شاسي[هة]?(?:ها)?|شاصي[هة]?|شصي|chassis|vin)"
                 r"|رقم\s*(?:ال)?(?:ماكين[هة]|ماكن[هة]|مكين[هة]|مكن[هة]|موتور)")
_CHASSIS = re.compile(r"(?:" + _CHASSIS_WORD + r")" + _FILL + _ID, re.I)
# one ordinary word between the label and its number: "شاسي الداف 142745"
_CHASSIS_GAP = re.compile(
    r"(?:" + _CHASSIS_WORD + r")\s+[" + AR + r"]{2,9}\s+" + _ID, re.I)
# the number written BEFORE its label: "206678 شاسي"
_CHASSIS_AFTER = re.compile(r"(?<![\dA-Za-z])(\d{4,})\s*(?:" + _CHASSIS_WORD + r")", re.I)
_LABEL_AT_END = re.compile(
    r"(?:" + _CHASSIS_WORD + r"|(?:ال)?لوح[هة])" + _FILL + r"$", re.I)
_PLATE_LABEL = re.compile(r"(?:ال)?لوح[هة]" + _FILL)
_PLATE_TOKEN = re.compile(r"\d+|[" + AR + r"]+|[A-Za-z]+|\S")
# plate shapes with no label: a single region letter carrying one digit
_PLATE_BARE = [
    re.compile(r"(?<![" + AR + r"\dA-Za-z])(\d{3,6})\s*([ء-هي])\s*(\d)(?![\d" + AR + r"])"),
    re.compile(r"(?<![" + AR + r"\dA-Za-z])([ء-هي])\s*(\d)\s*[/\\]?\s*(\d{3,6})(?![\dA-Za-z])"),
]
_TOKEN = re.compile(r"[0-9A-Za-z]+(?:[\-_][0-9A-Za-z]+)*")
_OR_ALT = re.compile(r"\s*[اأ]و\s+([0-9A-Za-z]{4,})(?![0-9A-Za-z])")
_OR_BETWEEN = re.compile(r"(?<=\d)\s+[اأ]و\s+(?=\d)")
# a list keyed by PLATE: "خ2 - 66477 - توسان", "خ3 - 105 - فلوكسواجن"
_PLATE_ROW = re.compile(
    r"^\s*([" + AR + r"]{1,2})\s*(\d{1,2})\s*-\s*(\d{3,6})\s*-\s*(.*)$")
_UNCLEAR = re.compile(r"ناقص|ممسوح|مطموس|غير واضح|ما ?واضح")

_FOUND = re.compile(r"موجود|لقينا|لقيت|عثر|وجدنا|وجدت|معثور|محجوز|حصر")
_LOST = re.compile(r"مفقود|مسروق|منهوب|فاقد|اتفقد|انسرق|سرق|ضاع|فقدت|فقد ")
_SALE = re.compile(r"للبيع|السعر|بسعر|عرض خاص")

LIST_LINE_WORDS = 7      # a list line is short: a model, a colour, a number
LIST_MIN_LINES = 3


_FB_CAPTION = re.compile(r"May be an image|No photo description|text that says", re.I)
_VEHICLE_WORDS = re.compile(
    r"عرب[يه]|سيار|مركب|شاسي|لوح[هة]|موديل|ركش|تكتك|بوكس|دفار|شاحن|حافل|مفقود|موجود")


def has_vehicle_vocab(line: str) -> bool:
    return bool(_VEHICLE_WORDS.search(line) or model_keys_in(line))


def _digits(tok: str) -> int:
    return sum(ch.isdigit() for ch in tok)


def _blank(s: str, a: int, b: int) -> str:
    return s[:a] + " " * (b - a) + s[b:]


def _scan_plate(s: str, pos: int) -> tuple[int, str] | None:
    """A plate starting at `pos` (just after its لوحة label): (end, text).

    Reads region letters, a region digit and the serial in any order, and
    stops at the first ordinary word. After the serial it accepts one region
    letter only if it is a single letter or carries a digit: "34810 من" is a
    plate followed by the word "from", not a plate with the region "من".
    """
    end, serial, markers, after = pos, False, 0, 0
    toks = list(_PLATE_TOKEN.finditer(s, pos))[:9]
    for i, m in enumerate(toks):
        tok = m.group(0)
        nxt = toks[i + 1].group(0) if i + 1 < len(toks) else ""
        if tok.isdigit():
            if serial:
                if len(tok) == 1 and after >= 1:
                    end = m.end()
                break
            if len(tok) > 7:
                return None
            if len(tok) >= 3:
                serial = True
            end = m.end()
        elif re.fullmatch("[" + AR + "]+", tok):
            if len(tok) > 2:
                break
            if serial:
                # a second single letter completes a two-letter marker written
                # with a space: "17122 ش ك"
                if after >= 2 or (after == 1 and len(tok) != 1):
                    break
                if not (len(tok) == 1 or (nxt.isdigit() and len(nxt) == 1)):
                    break
                # و is "and"; ت is "tel." in front of the phone number
                if tok in ("و", "ت"):
                    break
                after += 1
                end = m.end()
            else:
                markers += 1
                if markers > 3:
                    return None
        elif tok in "/\\.-":
            continue
        else:
            break
    if not serial:
        return None
    return end, s[pos:end].strip(" /\\.-")


_URL = re.compile(r"(?:https?://|www\.)\S+|\S+\.(?:com|me|net|org|online)(?:/\S*)?|@\w+", re.I)


def _mask_noise(line: str) -> tuple[str, str | None, int | None]:
    """Blank out links, phones, dates, prices, mileage: (work, contact, year)."""
    work, contact, year = line, None, None
    # links and handles first: the id in "whatsapp.com/channel/0029VaE7L5T..."
    # read as a VIN 491 times in the first benchmark
    for m in _URL.finditer(work):
        work = _blank(work, m.start(), m.end())
    for m in _CONTACT_LABEL.finditer(work):
        # "0912345678.  0123456789" is two numbers: keep the first whole one,
        # never the two glued into a number nobody can dial
        runs = re.findall(r"\d+", m.group(1))
        contact = contact or (runs[0] if len(runs[0]) >= 9 else "".join(runs))
        work = _blank(work, m.start(1), m.end(1))
    for pat in _PHONES:
        for m in pat.finditer(work):
            contact = contact or re.sub(r"\D", "", m.group(0))
            work = _blank(work, m.start(), m.end())
    for pat in (_DATE, _GROUPED):
        for m in pat.finditer(work):
            work = _blank(work, m.start(), m.end())
    return work, contact, year


def _mask_money(work: str) -> str:
    for pat in (_MEASURE_BEFORE, _MEASURE_AFTER):
        for m in pat.finditer(work):
            work = _blank(work, m.start(1), m.end(1))
    return work


def _mask_measures(work: str) -> tuple[str, int | None]:
    year = None
    work = _mask_money(work)
    for pat in (_SMALL_BEFORE, _SMALL_AFTER):
        for m in pat.finditer(work):
            work = _blank(work, m.start(1), m.end(1))
    for m in _YEAR.finditer(work):
        year = year or int(m.group(1))
        work = _blank(work, m.start(), m.end())
    return work, year


def _bare_tokens(work: str) -> list[str]:
    """Identifier-shaped tokens still standing: 5+ digits, or a VIN-like mix."""
    out = []
    for m in _TOKEN.finditer(work):
        tok = m.group(0)
        d = _digits(tok)
        if d >= 5 or (d >= 4 and len(tok) >= 8 and d < len(tok)):
            out.append(tok)
    return out


def _is_vin(tok: str) -> bool:
    t = re.sub(r"[\-_]", "", tok)
    alpha = [ch for ch in t if ch.isalpha()]
    # a stamped VIN is one case throughout; mixed case is a link or a username
    one_case = all(ch.isupper() for ch in alpha) or all(ch.islower() for ch in alpha)
    # exactly 17: shorter mixes are engine codes, battery models, usernames
    return len(t) == 17 and len(alpha) >= 2 and _digits(t) >= 5 and one_case


def _status(line: str, post: str) -> str:
    for src in (line, post):
        if _SALE.search(src):
            return "for_sale"
        if _FOUND.search(src):
            return "seeking_owner"
        if _LOST.search(src):
            return "unknown"
    return "unknown"


def _read_prose_line(line: str) -> tuple[list[dict], list[str]]:
    """One prose line -> (vehicles, unexplained numbers)."""
    work, contact, _ = _mask_noise(line)
    work = _mask_money(work)      # "لوحة = 1200 الف" is a price, not a plate
    chassis: list[tuple[str, str]] = []
    plates: list[str] = []

    for pat, rule in ((_CHASSIS, "label"), (_CHASSIS_GAP, "label_gap")):
        while True:
            m = pat.search(work)
            if not m:
                break
            tok, end = m.group(1), m.end()
            # "رقم الشاسي 33 151": one short number typed with a space in it
            sp = re.match(r" (\d{2,5})(?![\dA-Za-z])", work[end:])
            if tok.isdigit() and len(tok) < 4 and sp and not _YEAR.fullmatch(sp.group(1)):
                tok, end = tok + " " + sp.group(1), end + sp.end()
            if _digits(tok) >= 3 and not (rule == "label_gap" and _YEAR.fullmatch(tok)):
                chassis.append((tok, rule))
                # "الشاسي 10006182 او 01682": the poster's second reading
                alt = _OR_ALT.match(work[end:])
                if alt:
                    chassis.append((alt.group(1), rule))
                    end += alt.end()
                work = _blank(work, m.start(), end)
            else:
                # the label is not followed by a number here: step past it
                work = _blank(work, m.start(), m.start() + 2)
    m = _CHASSIS_AFTER.search(work)
    # "بوكسي 2014 شاسي ..." : a model year standing before the word, not a number
    if m and not chassis and not _YEAR.fullmatch(m.group(1)):
        chassis.append((m.group(1), "label_after"))
        work = _blank(work, m.start(), m.end())

    for m in list(_PLATE_LABEL.finditer(work)):
        got = _scan_plate(work, m.end())
        if got:
            plates.append(got[1])
            work = _blank(work, m.start(), got[0])
    work, year = _mask_measures(work)
    # an unlabelled plate shape counts only beside vehicle words: "600م2" is
    # a plot of land in square metres
    for pat in _PLATE_BARE if has_vehicle_vocab(line) else ():
        while True:
            m = pat.search(work)
            if not m or "و" in m.groups() or "م" in m.groups():
                break
            plates.append(" ".join(m.groups()))
            work = _blank(work, m.start(), m.end())

    unexplained = []
    for tok in _bare_tokens(work):
        if _is_vin(tok):
            chassis.append((tok, "vin"))
        else:
            unexplained.append(tok)

    vehicles = []
    desc = re.sub(r"\s+", " ", line).strip()[:300]
    keys = model_keys_in(line)
    base = {"model_key": keys[0] if keys else None, "year": year,
            "color": _colour_in(line), "contact": contact, "description": desc}
    paired = len(plates) == len(chassis) or (len(chassis) == 1 and len(plates) == 1)
    for i, (tok, rule) in enumerate(chassis):
        doubt = ({"low_confidence": "four digits that could be a model year"}
                 if _YEAR.fullmatch(tok) else {})
        vehicles.append({**base, **doubt, "chassis": tok, "rule": rule,
                         "plate": plates[i] if paired and i < len(plates) else None})
    if not chassis or not paired:
        for p in plates:
            vehicles.append({**base, "chassis": None, "plate": p, "rule": "plate"})
    return vehicles, unexplained


def _merge_split_lines(lines: list[str]) -> list[str]:
    """Join a number to the label or description it was written under.

        رقم الشاسي          بكس مستبيشي
        20702329690         214636
    """
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if out and _LABEL_AT_END.search(out[-1]) and re.match(r"[0-9A-Za-z]", s):
            out[-1] += " " + s
        elif (out and re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z\-_ ]{3,}", s)
              and _digits(s) >= 4 and not re.search(r"\d{4,}", _YEAR.sub("", out[-1]))
              and len(out[-1].split()) <= LIST_LINE_WORDS
              # only under a line that DESCRIBES a vehicle: glued to a chat
              # header ("ابو مهند: كشف جديد") the number was dropped with it
              and (model_keys_in(out[-1]) or _colour_in(out[-1]))
              and not any(p.search(s) for p in _PHONES)):
            out[-1] += " " + s
        else:
            out.append(s)
    return out


def _is_list_line(line: str) -> bool:
    if len(line.split()) > LIST_LINE_WORDS + 2 or _FB_CAPTION.search(line):
        return False
    # only the list parser's own phone filter here: this decides which reader
    # gets the line, and the broader prose filters must not hide a list line
    work, _ = _mask_measures(_PHONE.sub(" ", line))
    return any(_digits(t) >= 4 for t in _TOKEN.findall(work))


def extract(text: str) -> dict:
    """{"vehicles": [...], "settled": bool, "why": str, "mode": str}"""
    post = clean(text)
    lines = _merge_split_lines(post.splitlines())
    listy = [_is_list_line(l) for l in lines]
    list_mode = sum(listy) >= LIST_MIN_LINES

    vehicles, unexplained, seen = [], [], set()

    def add(v: dict) -> None:
        key = (v.get("chassis"), v.get("plate"))
        if key in seen:
            return
        tok = v.get("chassis") or ""
        digits = re.sub(r"\D", "", tok)
        if tok and len(set(digits)) <= 1:
            return                    # "00000000": a placeholder, not a number
        alpha = [ch for ch in tok if ch.isalpha()]
        mixed = any(ch.isupper() for ch in alpha) and any(ch.islower() for ch in alpha)
        if len(re.sub(r"[^0-9A-Za-z]", "", tok)) > 17 or (mixed and len(alpha) > len(digits)):
            unexplained.append(tok)   # a username, a glued pair: not this reader's call
            return
        seen.add(key)
        v["_comment"] = in_comments
        if _UNCLEAR.search(v.get("source_line") or v.get("description") or ""):
            v.setdefault("low_confidence",
                         "the post itself says the number is incomplete or unclear")
        vehicles.append(v)

    in_comments = False
    for line, is_list in zip(lines, listy):
        # Facebook prints "Shared post" where the post ends; what follows is
        # other people's comments, usually someone SEEKING a car under a list
        # of found ones
        if line.strip() == "Shared post":
            in_comments = True
            continue
        pr = _PLATE_ROW.match(line)
        if pr:
            letters, digit, serial, rest = pr.groups()
            keys = model_keys_in(rest)
            add({"chassis": None, "plate": f"{serial} {letters}{digit}",
                 "rule": "list" if list_mode else "plate",
                 "model_key": keys[0] if keys else None, "year": None,
                 "color": _colour_in(rest), "contact": None,
                 "description": rest.strip()[:300] or None})
            continue
        if _FB_CAPTION.search(line):
            # Facebook's machine caption of a photo. Its numbers are garbled
            # often enough ("شاسي 8886888", "شاسي 666666") that none is
            # emitted, and one identifier-shaped number sends the post on to
            # the photo OCR and a model.
            work = _mask_measures(_mask_noise(line)[0])[0]
            unexplained += [t for t in _TOKEN.findall(work) if _digits(t) >= 4]
            continue
        if list_mode and is_list:
            # a labelled plate is read by the plate scanner first: the list
            # parser files "لوحه خ4/ 1909" under years and loses it
            work, labelled = line, []
            for m in list(_PLATE_LABEL.finditer(line)):
                got = _scan_plate(work, m.end())
                if got and work[m.start():m.end()].strip():
                    labelled.append(got[1])
                    work = _blank(work, m.start(), got[0])
            # links, labelled contacts and foreign or dashed phones: the list
            # parser knows the plain local phone shape only
            work = _OR_BETWEEN.sub(" - ", _mask_noise(work)[0])
            rows = parse_line(work)
            for v in rows:
                if v.get("murky"):
                    v["low_confidence"] = "plate shape the list parser could not resolve"
                v["rule"] = "list"
            if labelled and len(labelled) == len(rows):
                for v, plate in zip(rows, labelled):
                    v["plate"] = v.get("plate") or plate
            elif labelled:
                keys = model_keys_in(line)
                for plate in labelled:
                    rows.append({"chassis": None, "plate": plate, "rule": "list",
                                 "model_key": keys[0] if keys else None,
                                 "year": None, "color": _colour_in(line),
                                 "contact": None, "description": line[:300]})
            for v in rows:
                add(v)
            continue
        found, left = _read_prose_line(line)
        for v in found:
            add(v)
        unexplained += left

    status_post = post[:600]
    # the post's contact number applies to every vehicle without its own
    post_contact = _mask_noise(post)[1]
    for v in vehicles:
        v["contact"] = v.get("contact") or post_contact
        if v["_comment"]:
            # a comment is judged by its own words, never by the post above it
            v["status"] = _status(v.get("source_line") or v.get("description") or "", "")
        v.setdefault("status", "found" if v["rule"] == "list"
                     else _status(v.get("description") or "", status_post))
    # One car described over several lines ("رقم الشاسي ..." then "رقم اللوحة
    # ..."): a prose post with exactly one chassis and one plate is one vehicle.
    if not list_mode and len(vehicles) == 2 and not any(v["_comment"] for v in vehicles):
        a, b = vehicles
        if bool(a.get("chassis")) != bool(b.get("chassis")) and not (a.get("plate") and b.get("plate")):
            car, other = (a, b) if a.get("chassis") else (b, a)
            if not car.get("plate") and other.get("plate"):
                car["plate"] = other["plate"]
                for f in ("model_key", "year", "color", "low_confidence"):
                    car[f] = car.get(f) or other.get(f)
                vehicles[:] = [car]
    if len(vehicles) == 1 and not vehicles[0]["_comment"]:
        v = vehicles[0]
        keys = model_keys_in(post)
        v["model_key"] = v.get("model_key") or (keys[0] if keys else None)
        v["color"] = v.get("color") or _colour_in(post)
        if not v.get("year"):
            m = _YEAR.search(_mask_noise(post)[0])
            v["year"] = int(m.group(1)) if m else None

    mode = "list" if list_mode else "prose"
    if unexplained:
        return {"vehicles": vehicles, "settled": False, "mode": mode,
                "why": "a number the rules cannot explain"}
    if not vehicles:
        keys = model_keys_in(post)
        if keys and _SALE.search(post):
            m = _YEAR.search(_mask_noise(post)[0])
            vehicles.append({
                "chassis": None, "plate": None, "rule": "ad",
                "model_key": keys[0], "year": int(m.group(1)) if m else None,
                "color": _colour_in(post), "status": "for_sale",
                "contact": _mask_noise(post)[1],
                "description": re.sub(r"\s+", " ", post).strip()[:300]})
    return {"vehicles": vehicles, "settled": True, "why": "", "mode": mode}
