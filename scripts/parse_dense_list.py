import json, re, sys, os

# canonical model map (best-effort). Keys are checked as substrings of the label after
# normalizing whitespace. Order matters: longer/more specific keys first.
MODEL_MAP = [
    # (arabic substrings tuple, make, model)
    (("كورولا","كوريلا","كورلا","كرولا","كريلا"), "Toyota", "Corolla"),
    (("سيريون","سيريوون"), "Daihatsu", "Sirion"),  # must precede "ريو" (Rio): "سيريون" contains "ريو" as a raw substring
    (("ياريس","يارس"), "Toyota", "Yaris"),
    (("كامري","حرجلي"), "Toyota", "Camry"),
    (("كرونا",), "Toyota", "Crown/Corona"),
    (("كريسيدا","كريس كريس","كريز"), "Toyota", "Cressida/Crown (كريز)"),
    (("راف فور","راف4"), "Toyota", "RAV4"),
    (("لاندكروزر","لاند كروزر","كروزر"), "Toyota", "Land Cruiser"),
    (("اف جي كروزر",), "Toyota", "FJ Cruiser"),
    (("هايلوكس",), "Toyota", "Hilux"),
    (("هايس",), "Toyota", "Hiace"),
    (("قبين",), "Toyota", "Hilux (قبين cab)"),
    (("افانتي","النترا","سحلية","منفوخة"), "Hyundai", "Elantra/Avante"),
    (("سوناتا",), "Hyundai", "Sonata"),
    (("توسان",), "Hyundai", "Tucson"),
    (("اكسنت","دبدوب","فيرنا"), "Hyundai", "Accent"),
    (("كلك","قيتز","جيتز","Getz"), "Hyundai", "Getz"),
    (("اتوس","اتوذ","Atos"), "Hyundai", "Atos"),
    (("اي تن","ايتن","i10"), "Hyundai", "i10"),
    (("اي تونتي","i20"), "Hyundai", "i20"),
    (("اي ثيرتي","i30"), "Hyundai", "i30"),
    (("سنتافي","سنتافيه","سانتافي"), "Hyundai", "Santa Fe"),
    (("ستاركس","استاركس","استاريكس","ستايركس"), "Hyundai", "Starex"),
    (("بورتر","فتون"), "Hyundai", "Porter"),
    (("مورنينق","مورنينج","مورنيق","مورني","مورلينج"), "Kia", "Picanto/Morning"),
    (("بيكانتو",), "Kia", "Picanto"),
    (("ريو","كيا ريو"), "Kia", "Rio"),
    (("سيراتو",), "Kia", "Cerato"),
    (("اسبورتاج","سبورتاج"), "Kia", "Sportage"),
    (("اومبتيما","اوبتيما"), "Kia", "Optima"),
    (("كيا راي",), "Kia", "Ray"),
    (("كبدى",), "Kia", "Carens/unclear(كبدى)"),
    (("لانسر","بومه"), "Mitsubishi", "Lancer"),
    (("باجيرو",), "Mitsubishi", "Pajero"),
    (("اوتلاندر",), "Mitsubishi", "Outlander"),
    (("جيلي",), "Geely", None),
    (("بي واي دي","BYD"), "BYD", None),
    (("شفروليت","شفرلي","شفرليت"), "Chevrolet", None),
    (("سبارك","اسبارك"), "Chevrolet", "Spark"),
    (("لافيتا",), "Chevrolet/Daewoo", "Lacetti"),
    (("ماتز",), "Daewoo", "Matiz"),
    (("نوبيرا",), "Daewoo", "Nubira"),
    (("بيجو",), "Peugeot", None),
    (("فولكسواجن","فلوكسواجن","فولكس واجن"), "Volkswagen", None),
    (("بولو",), "Volkswagen", "Polo"),
    (("باسات",), "Volkswagen", "Passat"),
    (("اسكودا","سكودا"), "Skoda", None),
    (("اوكتافيا",), "Skoda", "Octavia"),
    (("ساندرو",), "Renault", "Sandero"),
    (("ميقان",), "Renault", "Megane"),
    (("تيريوس","تيروس"), "Daihatsu", "Terios"),
    (("دايهاتسو",), "Daihatsu", None),
    (("سوزوكي التو","التو","Alto"), "Suzuki", "Alto"),
    (("سوزوكي",), "Suzuki", None),
    (("لاندروفر",), "Land Rover", None),
    (("ليكزس","لكزيز","لكزس"), "Lexus", None),
    (("جي ام سي","GMC"), "GMC", None),
    (("مرسيدس","مارسيدس"), "Mercedes-Benz", None),
    (("بي ام دبليو","BMW"), "BMW", None),
    (("MG","ام جي"), "MG", None),
    (("ايون","EON"), "Hyundai", "Eon"),
    (("جياد",), "Jiad", None),
    (("امجاد","شبيهة امجاد"), None, "Amjad van"),
    (("نيسان صني","صني"), "Nissan", "Sunny"),
    (("نيسان صالون","نيسان",), "Nissan", None),
    (("هوندا سيتي",), "Honda", "City"),
    (("هوندا","هوندا قيراني"), "Honda", None),
    (("مضلع",), None, "double-cab pickup (مضلع)"),
    (("شريحة","شريحه"), None, "van/minibus (شريحة)"),
    (("بوكس دبل","بوكسي","بوكس"), None, "pickup (بوكس)"),
    (("حافلة","باص"), None, "bus (حافلة)"),
    (("لوري","قلاب","تنكر وقود","شاحنة","ترلة","ترله"), None, "truck (لوري/قلاب/شاحنة)"),
    (("ركشة","تكتك","توكتوك"), None, "three-wheeler (ركشة)"),
    (("دفار",), None, "Land Cruiser pickup (دفار)"),
    (("راس دفار","راس"), None, "tractor head/راس"),
    (("لودر",), None, "loader (لودر)"),
    (("تراكتر",), None, "tractor"),
    (("بايك",), None, "motorbike (بايك)"),
    # bare-make fallbacks (checked only after every specific model above has
    # had a chance to match, and after the body-type special-case below)
    (("تايوتا","تويوتا"), "Toyota", None),
    (("كيا",), "Kia", None),
    (("متسوبيشي","ميتسوبيشي","ميتسوبيشى"), "Mitsubishi", None),
]

BODY_TYPE_WORDS = ["مضلع","مصلع","شريحة","شريحه","بوكس","بوكسي","حافلة","لوري","قلاب","ركشة","امجاد"]
BODY_TYPE_LABEL = {
    "مضلع": "double-cab pickup (مضلع)", "مصلع": "double-cab pickup (مضلع)",
    "شريحة": "van/minibus (شريحة)", "شريحه": "van/minibus (شريحة)",
    "بوكس": "pickup (بوكس)", "بوكسي": "pickup (بوكس)",
    "حافلة": "bus (حافلة)", "لوري": "truck (لوري)", "قلاب": "truck (قلاب)",
    "ركشة": "three-wheeler (ركشة)", "امجاد": "Amjad van",
}
BARE_MAKE_WORDS = [
    ("هونداي", "Hyundai"), ("هيونداي", "Hyundai"),
    ("تايوتا", "Toyota"), ("تويوتا", "Toyota"),
    ("كيا", "Kia"), ("نيسان", "Nissan"),
    ("متسوبيشي", "Mitsubishi"), ("ميتسوبيشي", "Mitsubishi"),
    ("مرسيدس", "Mercedes-Benz"), ("مارسيدس", "Mercedes-Benz"),
]

ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"

def map_label(label):
    lab = label.strip()
    # Accent's local nickname overrides the body-type word: "اكسنت مضلع" etc
    # must still resolve to Hyundai Accent, not a bare body type.
    if any(k in lab for k in ("اكسنت", "دبدوب", "فيرنا")):
        return "Hyundai", "Accent"
    # body-type words (مضلع/شريحة/بوكس/...) are never a model; pull a
    # co-occurring bare make word (e.g. "كيا مضلع") if present.
    for bw in BODY_TYPE_WORDS:
        if bw in lab:
            make = None
            for mw, mk in BARE_MAKE_WORDS:
                if mw in lab:
                    make = mk
                    break
            return make, BODY_TYPE_LABEL[bw]
    # bare Hyundai must be checked before Honda, since "هوندا" is a literal
    # substring of "هونداي" (Hyundai) and would otherwise misfire as Honda.
    if "هونداي" in lab or "هيونداي" in lab:
        return "Hyundai", None
    for keys, make, model in MODEL_MAP:
        for k in keys:
            if k in lab:
                if make == "Honda" and "هونداي" in lab:
                    continue
                return make, model
    return None, None

def is_number_token(tok):
    t = tok.strip("().:،,؛/")
    if not t:
        return False
    return all(c.isdigit() or c in ARABIC_INDIC for c in t)

NUM_RE = re.compile(r'[0-9٠-٩]{3,}')

def parse_text(text):
    """Return list of dicts: {label, color, chassis, plate, raw_line}"""
    out = []
    lines = text.split("\n")
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        # skip obvious non-vehicle lines
        if line.startswith("[") and "]" in line[:30]:
            # chat timestamp header like [٢٢/‏١ ١٢:١٠ ص] زولي شديد: كشف (A1)
            continue
        if any(kw in line for kw in ["واتس اب","واتساب","مجموعة رقم","اذا رغبت","للتواصل","اتصال","#شيركلغيرك","مكملين معاكم","كشف  (","كشف (","الكتابة"]):
            continue
        if line.startswith("#") or line.startswith("http"):
            continue

        # strip leading "N/" or "N-" numbering
        m_lead = re.match(r'^(\d+)[/\-]\s*(.*)$', line)
        if m_lead:
            line_body = m_lead.group(2).strip()
        else:
            line_body = line

        if not line_body:
            continue

        # format 2: "<label> (<color>) شاسي <num> [لوحة <num> <prefix>]"
        if "شاسي" in line_body:
            pre, _, post = line_body.partition("شاسي")
            label_part = pre.strip()
            color = None
            cm = re.search(r'\(([^)]*)\)', label_part)
            if cm:
                color = cm.group(1).strip()
                label_part = (label_part[:cm.start()] + label_part[cm.end():]).strip()
            chassis = None
            plate = None
            # post may contain: " 573432" then maybe "لوحة 64877 خ1"
            if "لوحة" in post or "لوحه" in post:
                post2 = post.replace("لوحه","لوحة")
                chas_part, _, plate_part = post2.partition("لوحة")
                nm = NUM_RE.search(chas_part)
                chassis = nm.group(0) if nm else None
                plate = plate_part.strip() or None
            else:
                nm = NUM_RE.search(post)
                chassis = nm.group(0) if nm else None
            out.append({"label": label_part, "color": color, "chassis": chassis, "plate": plate, "raw": line})
            continue

        # format 1: "<label> لوحه/لوحة <prefix>/ <num>"  or reversed "<num><prefix>"
        if "لوحه" in line_body or "لوحة" in line_body:
            body2 = line_body.replace("لوحه","لوحة")
            label_part, _, plate_part = body2.partition("لوحة")
            label_part = label_part.strip()
            plate_part = plate_part.strip()
            out.append({"label": label_part, "color": None, "chassis": None, "plate": plate_part, "raw": line})
            continue

        # format 1 plain: "<label> <num>" possibly with " او " alternatives inside label
        nm = NUM_RE.search(line_body)
        if nm:
            label_part = (line_body[:nm.start()] + line_body[nm.end():]).strip()
            if not label_part:
                # standalone number, no label
                out.append({"label": None, "color": None, "chassis": nm.group(0), "plate": None, "raw": line})
            else:
                out.append({"label": label_part, "color": None, "chassis": nm.group(0), "plate": None, "raw": line})
            continue
        # else: line has no number and doesn't look like vehicle data - skip
    return out

def to_vehicles(parsed, contact=None):
    vehicles = []
    for item in parsed:
        label = item["label"]
        make, model = (None, None)
        if label:
            make, model = map_label(label)
        veh = {
            "make": make,
            "model": model,
            "color": item["color"],
            "year": None,
            "chassis": item["chassis"],
            "plate": item["plate"],
            "status": "unknown",
            "location": None,
            "contact": contact,
            "description": (label or "") + (f" [raw: {item['raw']}]" if label != item['raw'] else "")
        }
        vehicles.append(veh)
    return vehicles

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("batchfile")
    ap.add_argument("--id", type=int, default=None)
    args = ap.parse_args()
    d = json.load(open(args.batchfile, encoding="utf-8"))
    for p in d:
        if args.id and p["id"] != args.id:
            continue
        parsed = parse_text(p["text"])
        print(f"=== post {p['id']}: {len(parsed)} parsed entries ===")
        for item in parsed[:20]:
            print(item)
