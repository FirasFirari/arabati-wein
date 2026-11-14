"""Free-text location -> Sudan admin-1 P-code (OCHA COD-AB, version v03).

Posts say where a vehicle was seen in whatever words the poster had: "امدرمان
الثورة", "Kordofan, Jabra Al-Sheikh", "داخل منزل". The open dataset
(scripts/build_open_dataset.py) reports counts per state, so each string is
mapped to the state P-code the humanitarian system already uses, or to an
explicit "could not be mapped" class. Nothing is guessed:

* a string naming places in two states is MULTIPLE, not the first one found;
* "Kordofan" or "Darfur" with no state is REGION_ONLY: three and five states
  share those names, and picking one would invent precision;
* a string naming no known place ("inside a house", an office, a village this
  table does not know) is UNMAPPED.

The gazetteer is deliberately short: states, their capitals, the localities
and the large neighbourhoods that actually occur in the corpus. A place is
added only when its state is certain. Matching is on whole words, longest
phrase first, so "جبرة الشيخ" (North Kordofan) is never read as "جبرة"
(Khartoum), "النيل الابيض" (White Nile) never as "الابيض" (El Obeid), and
"بربري" never as "بربر".

P-codes and names: "Sudan - Subnational Administrative Boundaries", OCHA,
https://data.humdata.org/dataset/cod-ab-sdn (CC BY-IGO).
"""
from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple

# pcode -> (English name, Arabic name), exactly as published in the COD-AB.
ADMIN1 = {
    "SD01": ("Khartoum", "الخرطوم"),
    "SD02": ("North Darfur", "شمال دارفور"),
    "SD03": ("South Darfur", "جنوب دارفور"),
    "SD04": ("West Darfur", "غرب دارفور"),
    "SD05": ("East Darfur", "شرق دارفور"),
    "SD06": ("Central Darfur", "وسط دارفور"),
    "SD07": ("South Kordofan", "جنوب كردفان"),
    "SD08": ("Blue Nile", "النيل الأزرق"),
    "SD09": ("White Nile", "النيل الأبيض"),
    "SD10": ("Red Sea", "البحر الأحمر"),
    "SD11": ("Kassala", "كسلا"),
    "SD12": ("Gedaref", "القضارف"),
    "SD13": ("North Kordofan", "شمال كردفان"),
    "SD14": ("Sennar", "سنار"),
    "SD15": ("Aj Jazirah", "الجزيرة"),
    "SD16": ("River Nile", "نهر النيل"),
    "SD17": ("Northern", "الشمالية"),
    "SD18": ("West Kordofan", "غرب كردفان"),
    "SD19": ("Abyei PCA", "إدارية أبيي"),
}

MULTIPLE = "multiple"
REGION_ONLY = "region_only"
COUNTRY_ONLY = "country_only"
UNMAPPED = "unmapped"

_GAZETTEER = {
    "SD01": """
        الخرطوم | خرطوم | khartoum | امدرمان | ام درمان | امدر | omdurman
        | بحري | بحرى | bahri | العاصمه | capital | كوبر | kober | koper | cooper
        | الكلاكله | الكلاكلات | kalakla | شرق النيل | east nile | sharg an neel
        | جبل اولياء | جبل اوليا | jabal awliya | jebel awlia | صالحه | الصالحه
        | الجموعيه | الحلفايا | الحلفايه | halfaya | الكدرو | الازهري | azhari
        | اركويت | arkwit | الصحافه | كرري | karrari | امبده | الحاج يوسف
        | سوبا | soba | الفتيحاب | سوق ليبيا | شمبات | كافوري | المعموره
        | الجريف | جبره | jabra | بري | burri
    """,
    "SD02": "شمال دارفور | north darfur | الفاشر | el fasher | al fashir",
    "SD03": "جنوب دارفور | south darfur | نيالا | nyala",
    "SD04": "غرب دارفور | west darfur | الجنينه | geneina",
    "SD05": "شرق دارفور | east darfur | الضعين | ed daein",
    "SD06": "وسط دارفور | central darfur | زالنجي | zalingei",
    "SD07": "جنوب كردفان | south kordofan | كادقلي | kadugli | الدلنج | dilling",
    "SD08": "النيل الازرق | blue nile | الدمازين | damazin",
    "SD09": """
        النيل الابيض | white nile | كوستي | kosti | ربك | rabak | الدويم
        | القطينه | الجزيره ابا
    """,
    "SD10": "البحر الاحمر | red sea | بورتسودان | بورسودان | port sudan | سواكن | suakin",
    "SD11": "كسلا | kassala | حلفا الجديده | new halfa",
    "SD12": "القضارف | gedaref | الحواته | hawata",
    "SD13": """
        شمال كردفان | north kordofan | بارا | باره | bara | جبره الشيخ
        | جبرا الشيخ | jabra al sheikh | gebrat al sheikh | ام قرفه | umm qirfa
        | حمره الوز | ام سياله | um siyala | الابيض | el obeid
    """,
    "SD14": """
        سنار | sennar | سنجه | sinja | singa | sinjah | الدندر | dinder
        | السوكي
    """,
    "SD15": """
        الجزيره | gezira | jazira | jazirah | مدني | ود مدني | واد مدني | madani
        | medani | الحصاحيصا | hasahisa | رفاعه | rufaa | تمبول | tambul
        | الكاملين | kamlin | المناقل | managil | طابت | ودراوه | wad rawah
        | جياد | jiad | giad
    """,
    "SD16": """
        نهر النيل | river nile | عطبره | atbara | شندي | shendi | بربر | berber
        | الدامر | damer
    """,
    "SD17": """
        الولايه الشماليه | الشماليه | northern state | دنقلا | dongola | مروي
        | merowe | الدبه
    """,
    "SD18": "غرب كردفان | west kordofan | غبيش | الفوله | النهود | en nahud",
}

# Names shared by several states. Reported as REGION_ONLY when no state of the
# region is named in the same string.
_REGIONS = {
    "Kordofan": ("كردفان | kordofan", ("SD07", "SD13", "SD18")),
    "Darfur": ("دارفور | darfur", ("SD02", "SD03", "SD04", "SD05", "SD06")),
}
_COUNTRY = "السودان | sudan"

# "الجزيرة اسلانج" is an island in Omdurman and "الجزيرة الخضراء" is not the
# state either: the bare word الجزيرة must not claim them for Aj Jazirah.
# "طارق كوبر" is the name of an office that compiles lists; whether كوبر there
# is the Bahri neighbourhood or a nickname is not known, so it maps nothing.
_NOT_A_PLACE = "الجزيره اسلانج | jazira islang | الجزيره الخضراء | طارق كوبر"

_TASHKEEL = re.compile(r"[ً-ٰٟـ]")
_FOLD = str.maketrans("أإآٱىة", "اااايه")
_SPLIT = re.compile(r"[^a-zء-ي]+")
_PREFIX = "وبلف"


def _tokens(text: str) -> list[str]:
    t = unicodedata.normalize("NFKC", str(text or "")).lower()
    t = _TASHKEEL.sub("", t).translate(_FOLD)
    return [tok for tok in _SPLIT.split(t) if tok]


def _forms(tok: str) -> set[str]:
    """A written Arabic word, with and without a one-letter prefix and ال."""
    out = {tok}
    if tok[:1] in _PREFIX and len(tok) > 3:
        out.add(tok[1:])
    for f in list(out):
        if f.startswith("ال") and len(f) > 4:
            out.add(f[2:])
    return out


def _phrases(spec: str) -> list[tuple[str, ...]]:
    return [tuple(_tokens(p)) for p in spec.split("|") if p.strip()]


def _compile() -> list[tuple[tuple[str, ...], str]]:
    table = []
    for code, spec in _GAZETTEER.items():
        table += [(p, code) for p in _phrases(spec)]
    for region, (spec, _) in _REGIONS.items():
        table += [(p, "region:" + region) for p in _phrases(spec)]
    table += [(p, "country") for p in _phrases(_COUNTRY)]
    table += [(p, "skip") for p in _phrases(_NOT_A_PLACE)]
    # longest phrase first, so a two-word name wins over a word inside it
    return sorted(table, key=lambda e: -len(e[0]))


_TABLE = _compile()


def _kw_match(kw: str, forms: set[str]) -> bool:
    if kw in forms:
        return True
    return kw.startswith("ال") and len(kw) > 4 and kw[2:] in forms


class Location(NamedTuple):
    kind: str                    # a P-code, or MULTIPLE / REGION_ONLY / COUNTRY_ONLY / UNMAPPED
    pcodes: tuple[str, ...]      # every state named (one for a P-code, several for MULTIPLE)
    region: str | None           # "Kordofan" / "Darfur" when kind is REGION_ONLY


def locate(text: str) -> Location:
    """Map one free-text location to a state P-code, or say why it cannot be."""
    toks = _tokens(text)
    forms = [_forms(t) for t in toks]
    used = [False] * len(toks)
    hits: list[str] = []
    for phrase, label in _TABLE:
        n = len(phrase)
        for i in range(len(toks) - n + 1):
            if any(used[i:i + n]):
                continue
            if all(_kw_match(phrase[k], forms[i + k]) for k in range(n)):
                used[i:i + n] = [True] * n
                hits.append(label)
    states = sorted({h for h in hits if h in ADMIN1})
    regions = sorted({h[7:] for h in hits if h.startswith("region:")})
    # a region word next to one of its own states adds nothing ("شمال كردفان
    # بارا"); next to another region's state it is a second place
    loose = [r for r in regions if not set(_REGIONS[r][1]) & set(states)]
    if len(states) == 1 and not loose:
        return Location(states[0], (states[0],), None)
    if states:
        return Location(MULTIPLE, tuple(states), None)
    if len(loose) == 1:
        return Location(REGION_ONLY, (), loose[0])
    if loose:
        return Location(MULTIPLE, (), None)
    if "country" in hits:
        return Location(COUNTRY_ONLY, (), None)
    return Location(UNMAPPED, (), None)
