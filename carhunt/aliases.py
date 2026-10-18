"""Canonical vehicle-model aliases for Sudanese Arabic.

Handles spelling drift (كورولا/كورلا/كرلا/كريلا) and local nicknames
(سحلية = Avante XD, مضلع = Hilux double-cab...). Matching layers:
1. alias table lookup (normalized)
2. consonant skeleton: strip long vowels ا/و/ي so all Corolla spellings
   reduce to كرل
The exported search page mirrors these rules in JS.
"""
import re

# canonical key -> aliases (Arabic variants, nicknames, English)
ALIASES: dict[str, list[str]] = {
    # كوريلا is the COMMONEST spelling in this corpus (875 posts) — more than
    # كورولا and كورلا — while كرلا and كريلا appear zero times. Without it a
    # terse post ("كوريلا ابيض 2015") carries no vehicle vocabulary and the
    # strict-source prefilter drops it for free, unseen.
    "toyota corolla": ["corolla", "كورولا", "كورلا", "كرلا", "كريلا", "كرولا",
                       "كوريلا"],
    "toyota hilux": ["hilux", "هايلوكس", "مضلع"],
    "toyota land cruiser": ["land cruiser", "landcruiser", "لاندكروزر", "لاند كروزر", "دفار"],
    "toyota camry": ["camry", "كامري", "حرجلي"],
    "toyota hiace": ["hiace", "هايس"],
    "toyota rav4": ["rav4", "راف فور", "راففور"],
    "toyota cressida": ["cressida", "كرسيدا"],
    "toyota corona": ["corona", "كرونا"],
    "toyota yaris": ["yaris", "يارس", "ياريس"],
    "hyundai accent": ["accent", "اكسنت", "فيرنا", "دبدوب"],
    "hyundai avante": ["avante", "elantra", "افانتي", "النترا", "سحلية", "سحليه", "منفوخة", "منفوخه"],
    "hyundai getz": ["getz", "click", "كلك", "قيتز"],
    "hyundai atos": ["atos", "اتوز"],
    "hyundai i10": ["i10", "اي تن", "ايتن"],
    "hyundai i20": ["i20", "اي تويني", "ايتويني"],
    "hyundai tucson": ["tucson", "توسان", "توسون"],
    "hyundai santa fe": ["santa fe", "santafe", "سنتافي", "سنتافى"],
    "hyundai sonata": ["sonata", "سوناتا"],
    "kia picanto": ["picanto", "morning", "بيكانتو", "مورنينق", "مورنينج", "مورنينغ"],
    "kia rio": ["rio", "ريو"],
    "kia cerato": ["cerato", "سيراتو"],
    "kia visto": ["visto", "فيستو"],
    "nissan sunny": ["sunny", "صني"],
    "nissan patrol": ["patrol", "باترول"],
    "nissan tiida": ["tiida", "تيدا"],
    "mitsubishi lancer": ["lancer", "لانسر", "بومه", "بومة"],
    "daewoo lacetti": ["lacetti", "لاسيتا", "لاسيتي"],
    "daewoo kalos": ["kalos", "كالوس"],
    "giad": ["giad", "جياد", "امجاد"],
    # BODY TYPES, not models. Sudanese posts name the body and (sometimes) the
    # make separately — "شريحه تايوتا", "شريحه نيسان", "شريحة صينية". Keeping
    # them as their own keys stops a van being filed under a sedan.
    "van/minibus (شريحة)": ["شريحة", "شريحه", "شرحة", "شريحت"],
    "pickup (بوكس)": ["بوكس", "بوكسي", "بكسي", "بكس", "pickup"],
    "bus (حافلة)": ["حافلة", "حافله", "باص", "bus"],
    "truck (لوري)": ["لوري", "شاحنة", "شاحنه", "truck", "قلاب"],
    "rickshaw": ["ركشة", "ركشه", "تكتك", "بجاج", "rickshaw", "tuktuk", "دقو ورا"],
    "byd": ["byd", "بي واي دي"],
    "geely": ["geely", "جيلي"],
}

_AR_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي",
                          "ؤ": "و", "ئ": "ي", "ـ": None})


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


def model_keys_in(text: str) -> list[str]:
    """All canonical model keys detectable in free text."""
    n = normalize_ar(text or "")
    found = []
    for alias, key in _ALIAS_EXACT.items():
        if alias in n and key not in found:
            found.append(key)
    # skeleton pass over individual words for unseen spellings
    for word in re.findall(r"[؀-ۿ]{3,}", n):
        key = _ALIAS_SKEL.get(skeleton(word))
        if key and key not in found:
            found.append(key)
    return found


def model_key_for(make: str | None, model: str | None, description: str | None = None) -> str | None:
    """Best canonical key for one vehicle record."""
    for source in (f"{make or ''} {model or ''}", description or ""):
        keys = model_keys_in(source)
        if keys:
            return keys[0]
    return None
