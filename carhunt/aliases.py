"""Canonical vehicle-model aliases for Sudanese Arabic.

Handles spelling drift (كورولا/كورلا/كرلا/كريلا) and local nicknames
(سحلية = Avante XD, مضلع = the angular Accent...). Matching layers:
1. alias table lookup (normalized)
2. consonant skeleton: strip long vowels ا/و/ي so all Corolla spellings
   reduce to كرل
The exported search page mirrors these rules in JS.
"""
import re

# canonical key -> aliases (Arabic variants, nicknames, English)
#
# AUDITED 2026-11-20 against 1.36 million stored descriptions. What that found,
# so nobody re-adds it:
#   * دفار is a LIGHT TRUCK (it stands beside بورتر, جامبو, ايسوزو, تلاجه), not
#     a Land Cruiser: 20,272 rows, 57% of everything labelled Land Cruiser.
#   * امجاد is a MINIVAN type (beside داماس, بايك). جياد is the assembler GIAD,
#     a make and not a model: 18,451 of its 25,734 rows stand beside اكسنت.
#   * حرجلي is a COLOUR (beside ten different models), not a Camry.
#   * كريز / كريس / قريس is the Hyundai Grace minibus.
#   * "اي تويني" was a typo that never occurred and whose skeleton collided
#     with "اي تن", so Arabic i20 spellings fell to the i10.
#   * the commonest spellings of Picanto (مورنق, مورنغ, مورنيق), Atos (اتوس),
#     Geely (جلي) and Land Cruiser (كروزر, برادو) were missing: ~45,000 rows.
# All four meanings above are the owner's rulings of that day.
ALIASES: dict[str, list[str]] = {
    # كوريلا is the COMMONEST spelling in this corpus (875 posts) — more than
    # كورولا and كورلا — while كرلا and كريلا appear zero times. Without it a
    # terse post ("كوريلا ابيض 2015") carries no vehicle vocabulary and the
    # strict-source prefilter drops it for free, unseen.
    "toyota corolla": ["corolla", "كورولا", "كورلا", "كرلا", "كريلا", "كرولا",
                       "كوريلا"],
    "toyota hilux": ["hilux", "هايلوكس", "هايلكس", "هيلوكس"],
    # جكس / جي اكس ار is the GXR, a Land Cruiser trim (owner, 2026-11-20)
    "toyota land cruiser": ["land cruiser", "landcruiser", "prado", "gxr", "لاندكروزر",
                            "لاند كروزر", "كروزر", "برادو", "جكس", "جي اكس ار"],
    "toyota camry": ["camry", "كامري"],
    "toyota hiace": ["hiace", "هايس"],
    "toyota rav4": ["rav4", "راف فور", "راففور", "رافور"],
    "toyota cressida": ["cressida", "كرسيدا", "كريسيدا"],
    "toyota corona": ["corona", "كرونا"],
    "toyota yaris": ["yaris", "يارس", "ياريس"],
    "toyota fortuner": ["fortuner", "فورشنر"],
    # مضلع ("angular") on its own is an Accent (owner, 2026-11-20). Until then
    # the table had it as a Toyota Hilux double-cab, and 45,000 rows said so.
    # It is a WEAK alias (see WEAK_ALIASES): any model or body type named
    # beside it wins, so "افانتي مضلع" is an Avante and "بوكسي تايوتا مضلع"
    # stays a pickup.
    "hyundai accent": ["accent", "اكسنت", "اكسن", "اكسند", "فيرنا", "دبدوب", "مضلع"],
    "hyundai avante": ["avante", "elantra", "افانتي", "افانت", "النترا", "الانترا",
                       "ايلانترا", "سحلية", "سحليه", "منفوخة", "منفوخه"],
    "hyundai getz": ["getz", "click", "كلك", "كليك", "كيلك", "قيتز"],
    "hyundai atos": ["atos", "اتوز", "اتوس", "اتس", "اتوذ"],
    "hyundai i10": ["i10", "اي تن", "ايتن", "اي تين", "ايتين"],
    "hyundai i20": ["i20", "اي توينتي", "ايتونتي", "اي تونتي"],
    "hyundai tucson": ["tucson", "توسان", "توسون"],
    "hyundai santa fe": ["santa fe", "santafe", "سنتافي", "سنتافى", "سانتافي"],
    "hyundai sonata": ["sonata", "سوناتا", "سونتا"],
    "hyundai starex": ["starex", "استايركس", "استاريكس", "ستايركس", "استاركس",
                       "ستاركس", "استريكس"],
    "hyundai grace": ["grace", "كريز", "كريس", "قريس", "قريز"],
    "hyundai porter": ["porter", "بورتر"],
    "hyundai eon": ["eon", "ايون"],
    "hyundai h1": ["h1", "اتش ون"],
    "hyundai lavita": ["lavita", "لافيتا"],
    # كراتو / كريتو: "probably Creta" (owner, 2026-11-20), not Cerato
    "hyundai creta": ["creta", "كريتا", "كراتو", "كريتو", "كيراتو"],
    "kia picanto": ["picanto", "morning", "بيكانتو", "بكانتو", "مورنينق", "مورنينج",
                    "مورنينغ", "مورننق", "مورننج", "مورننغ", "مورنق", "مورنغ",
                    "مورنيق", "مورينق"],
    "kia rio": ["rio", "ريو"],
    "kia cerato": ["cerato", "سيراتو", "سراتو"],
    "kia pride": ["pride", "برايد"],
    "kia visto": ["visto", "فيستو", "فستو", "فيست"],
    "kia sportage": ["sportage", "اسبورتاج", "سبورتاج"],
    "kia optima": ["optima", "اوبتما", "اوبتيما"],
    "nissan sunny": ["sunny", "صني", "سوني"],
    "nissan patrol": ["patrol", "باترول"],
    "nissan tiida": ["tiida", "تيدا"],
    # قرش ("shark") is the Lancer's nickname: 3,694 rows write it beside لانسر.
    # WEAK, because the word also means a coin.
    "mitsubishi lancer": ["lancer", "لانسر", "لانصر", "بومه", "بومة", "قرش"],
    # باجوري is the Pajero too (owner, 2026-11-20)
    "mitsubishi pajero": ["pajero", "باجيرو", "باجورو", "باجوري"],
    "mitsubishi attrage": ["attrage", "اتراج"],
    "daewoo lacetti": ["lacetti", "لاسيتا", "لاسيتي"],
    "daewoo kalos": ["kalos", "كالوس"],
    "daewoo nubira": ["nubira", "نوبيرا"],
    "daihatsu terios": ["terios", "تيريوس", "تريوس", "تيروس"],
    "renault duster": ["duster", "دوستر", "دستر", "داستر"],
    "chevrolet spark": ["spark", "اسبارك"],
    "volkswagen polo": ["polo", "بولو"],
    # MAKES with no model of their own in this table. GIAD assembles other
    # makers' cars in Sudan ("اكسنت جياد"), so a model named beside it wins.
    "giad": ["giad", "جياد"],
    "byd": ["byd", "بي واي دي"],
    "geely": ["geely", "جيلي", "جلي"],
    # BODY TYPES, not models. Sudanese posts name the body and (sometimes) the
    # make separately — "شريحه تايوتا", "شريحه نيسان", "شريحة صينية". Keeping
    # them as their own keys stops a van being filed under a sedan.
    "van/minibus (شريحة)": ["شريحة", "شريحه", "شرحة", "شريحت"],
    "pickup (بوكس)": ["بوكس", "بوكسي", "بكسي", "بكس", "بكاسي", "pickup"],
    "light truck (دفار)": ["دفار"],
    "minivan (امجاد)": ["امجاد"],
    "bus (حافلة)": ["حافلة", "حافله", "باص", "bus"],
    "truck (لوري)": ["لوري", "شاحنة", "شاحنه", "truck", "قلاب"],
    # دغري is a rickshaw, unless the post says "دفار دغري" (owner, 2026-11-20):
    # the light truck stands above the rickshaw in GENERIC_KEYS, so it wins.
    "rickshaw": ["ركشة", "ركشه", "تكتك", "توكتوك", "توكتك", "بجاج", "باجاج",
                 "rickshaw", "tuktuk", "دقو ورا", "دغري"],
    "sedan (صالون)": ["صالون"],
}

# How a key is written into a row's make and model fields. The parsers used to
# split the key on its first space, which put a BODY TYPE in the make field:
# 41,101 rows said make "Pickup", model "(بوكس)", and 14,457 make "Rickshaw".
_DISPLAY = {"byd": ("BYD", None), "giad": ("GIAD", None), "geely": ("Geely", None),
            "hyundai i10": ("Hyundai", "i10"), "hyundai i20": ("Hyundai", "i20"),
            "hyundai h1": ("Hyundai", "H1"), "toyota rav4": ("Toyota", "RAV4")}


def make_model_for(key: str | None) -> tuple[str | None, str | None]:
    """(make, model) to store for a key. A body type has no make: the key
    itself goes in the model field, as the model-read rows already do."""
    if not key:
        return None, None
    if key in _DISPLAY:
        return _DISPLAY[key]
    if key in GENERIC_KEYS:
        return None, key
    make, _, model = key.partition(" ")
    return make.title(), (model.title() or None)


# Persian kaf, gaf-less yeh and friends are the same letters to a reader:
# "اکسنت" (Persian kaf) was not an Accent, 254 rows.
_AR_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي",
                          "ؤ": "و", "ئ": "ي", "ـ": None,
                          "\u06a9": "\u0643", "\u06cc": "\u064a"})


def normalize_ar(text: str) -> str:
    """Lowercase, fold Arabic letter variants, strip diacritics."""
    text = (text or "").lower().translate(_AR_FOLD)
    return re.sub(r"[ً-ٰٟ]", "", text)


def skeleton(word: str) -> str:
    """Consonant skeleton: drop long vowels ا/و/ي (keeps first char).
    كورولا, كورلا, كرلا, كريلا -> كرل"""
    word = normalize_ar(word)
    if not word:
        return ""
    return word[0] + re.sub(r"[اوي]", "", word[1:])


# precomputed lookup tables
_ALIAS_EXACT: dict[str, str] = {}
_ALIAS_SKEL: dict[str, str] = {}
for key, names in ALIASES.items():
    for name in names + [key]:
        n = normalize_ar(name)
        _ALIAS_EXACT[n] = key
        s = skeleton(name)
        if len(s) >= 3:
            _ALIAS_SKEL.setdefault(s, key)


# --- matching rules (2026-11-20) --------------------------------------------
# The page's modelKeysIn (site/src/index.template.html) mirrors rules 1 and 2;
# tests/test_js_parity.py runs both over the same inputs. Change one, change
# both. (Rule 3 is order, and the page only asks "is this key present".)
#
# 1. WORDS, NOT SUBSTRINGS. An alias used to match anywhere inside the text:
#    "تيريوس" and "Terios" were a Kia Rio (they contain ريو / rio), "جربوكس"
#    (gearbox) was a pickup (بوكس), "صنعت" (made in) a Nissan Sunny (صني),
#    "bushara" a bus. An alias must now START a word (after an optional Arabic
#    prefix: ال، و، ب، ل) and END one -- except that an alias of 4+ letters may
#    run on into the same word, which keeps plurals and glued text working
#    ("دفارات", "دفاررقم", "النتراء"). Digits and a change of script count as
#    word ends ("كورلا2007", "i10ابيض").
# 2. COLOURS ARE NOT MODELS. The skeleton pass reduces the colour "فيراني"
#    (beige) to فرن, the skeleton of "فيرنا" (Verna), so every beige car with
#    no model beside it became a Hyundai Accent. Words in SKELETON_STOP are
#    never skeleton-matched.
# 3. ORDER. Keys come back specific models first, in the order the text names
#    them; then GIAD; then body types in table order. Callers take the first:
#    "اكسنت مضلع" used to be a Toyota Hilux, because Hilux stands above Accent
#    in the table. 32,425 list rows carried that label.
ARABIC_PREFIXES = ("ال", "وال", "بال", "لل", "و", "ب", "ل")
RUN_ON_MIN = 4

# A nickname for a body shape: it names a model only when nothing else does.
WEAK_ALIASES = frozenset({"مضلع", "قرش"})
# The assembler: a model named beside it wins.
_SECOND_TIER = frozenset({"giad"})
# Body types and vehicle classes: they describe a vehicle, a model names it.
# The order is the priority when a post names two of them.
GENERIC_KEYS = ("van/minibus (شريحة)", "pickup (بوكس)", "light truck (دفار)",
                "minivan (امجاد)", "bus (حافلة)", "truck (لوري)", "rickshaw",
                "sedan (صالون)")

SKELETON_STOP = frozenset(normalize_ar(w) for w in (
    "ابيض", "بيضاء", "بيضا", "اسود", "سوداء", "سودا", "فضي", "فضية", "سلفر",
    "رمادي", "رصاصي", "كحلي", "كحلية", "ازرق", "زرقاء", "اخضر", "خضراء",
    "احمر", "حمراء", "ذهبي", "ذهبية", "دهبي", "بني", "فيراني", "فئراني",
    "لبني", "لبنية", "شمباني", "سماوي", "زيتي", "عنابي", "بيج",
    "اصفر", "صفراء", "برتقالي", "بحريني", "كبدي", "ظهري", "زهري",
    "حرجلي", "فرياني", "فيران", "فراني", "فيرانا", "فيرني",
    # ordinary words whose skeleton is a model's (rows that carried the label):
    # "cars" was a Kia Cerato (53), "camera" a Camry (23), "crane"/"crown"/
    # "Carina" a Corona, "is not" a Lacetti, "class" a Kalos, "overturned" a
    # truck, "Santa" a Sonata, a misspelt Terios a Yaris
    "سيارات", "كاميرا", "كرين", "كراون", "كارينا", "ليست", "لست", "كلاس",
    "قالب", "قلب", "سانتا", "يريوس", "سونيت",
    # found by re-running the audit after the new models were added: each new
    # key brings its own look-alikes. A Kia Pride, "cold" and "post" were a
    # Prado; a Rover a RAV4; a Cruze, "chairs" and "cards" a Grace or a Creta;
    # "gears" a Terios; "constitution" a Duster; "petrol" a Patrol; "years" a
    # Sonata; Quraish and "piasters" a Lancer.
    "بارد", "بريد", "براد", "روفر", "كروز", "كروس", "كروزي", "كراسي",
    "كرسي", "كروت", "كرت", "كارتو", "ترس", "تروس", "تريس", "دستور", "بترول",
    "سنوات", "لفت", "لفيات", "قريش", "قروش", "قروشا", "فيرياني", "كمر",
    # كروز stays unlabelled: the owner will rule after seeing a photo.
))


def _is_ar(ch: str) -> bool:
    return ("\u0621" <= ch <= "\u064a") or ("\u066e" <= ch <= "\u06d3")


def _is_lat(ch: str) -> bool:
    return "a" <= ch <= "z"


def _is_dig(ch: str) -> bool:
    return ("0" <= ch <= "9") or ("\u0660" <= ch <= "\u0669") or ("\u06f0" <= ch <= "\u06f9")


def _starts_word(n: str, i: int, arabic: bool) -> bool:
    """Is `i` the start of a word (same script), or just after an Arabic prefix?"""
    same = _is_ar if arabic else _is_lat
    if i == 0 or not same(n[i - 1]):
        return True
    if not arabic:
        return False
    for p in ARABIC_PREFIXES:
        j = i - len(p)
        if j >= 0 and n.startswith(p, j) and (j == 0 or not _is_ar(n[j - 1])):
            return True
    return False


def _ends_word(n: str, j: int, alias: str) -> bool:
    if j == len(n):
        return True
    ch, last = n[j], alias[-1]
    if _is_dig(last):                      # "i10" is not the start of "i100"
        return not _is_dig(ch) and not _is_lat(ch)
    same = _is_ar if _is_ar(last) else _is_lat
    return not same(ch) or len(alias) >= RUN_ON_MIN


def _find_word(n: str, alias: str) -> int:
    """Index of the first word-level occurrence of `alias` in `n`, or -1."""
    arabic = _is_ar(alias[0])
    i = n.find(alias)
    while i != -1:
        if _ends_word(n, i + len(alias), alias) and _starts_word(n, i, arabic):
            return i
        i = n.find(alias, i + 1)
    return -1


def _skeleton_stopped(word: str) -> bool:
    return word in SKELETON_STOP or (word.startswith("ال") and word[2:] in SKELETON_STOP)


def model_keys_in(text: str) -> list[str]:
    """Canonical model keys named in free text; see the ORDER rule above."""
    n = normalize_ar(text or "")
    at: dict[str, int] = {}
    span: dict[str, int] = {}          # longest alias that matched at that place
    strong = set()
    for alias, key in _ALIAS_EXACT.items():
        i = _find_word(n, alias)
        if i == -1:
            continue
        if alias not in WEAK_ALIASES:
            strong.add(key)
        if i < at.get(key, len(n) + 1) or (i == at[key] and len(alias) > span[key]):
            at[key], span[key] = i, len(alias)
    # skeleton pass over individual words for unseen spellings
    for m in re.finditer(r"[\u0600-\u06ff]{3,}", n):
        word = m.group(0)
        if _skeleton_stopped(word):
            continue
        key = _ALIAS_SKEL.get(skeleton(word))
        if key and key not in at and word not in WEAK_ALIASES:
            at[key] = m.start()
            strong.add(key)

    has_body_type = any(k in GENERIC_KEYS for k in at)

    def rank(key: str):
        if key not in strong:
            # named only by a weak nickname: it yields to a body type named
            # beside it ("بوكسي مضلع" is a pickup) but not to the assembler
            # ("مضلع جياد" is a GIAD-built Accent)
            return (4 if has_body_type else 1, at[key], 0)
        if key in GENERIC_KEYS:
            return (3, GENERIC_KEYS.index(key), 0)
        # two models starting at the same place: the longer alias is the word
        # actually written ("كريسيدا" is a Cressida, not كريس + letters)
        return (2 if key in _SECOND_TIER else 0, at[key], -span.get(key, 0))
    return sorted(at, key=rank)


def model_key_for(make: str | None, model: str | None, description: str | None = None) -> str | None:
    """Best canonical key for one vehicle record."""
    for source in (f"{make or ''} {model or ''}", description or ""):
        keys = model_keys_in(source)
        if keys:
            return keys[0]
    return None
