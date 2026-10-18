#!/usr/bin/env python3
"""Parse dense كشف (numbered chassis-list) text into vehicle dicts.
Reusable across posts in this batch run. Alignment: 'NUMBER/ MODEL (COLOR) شاسي CHASSIS [لوحة PLATE AREA]'
confirmed by separator position (periods/slashes follow the digit first)."""
import json, re, sys

COLOR_MAP = {
    'ابيض': 'white', 'أبيض': 'white', 'اسود': 'black', 'أسود': 'black',
    'سلفر': 'silver', 'فضي': 'silver', 'ذهبي': 'gold', 'احمر': 'red', 'أحمر': 'red',
    'ازرق': 'blue', 'أزرق': 'blue', 'اخضر': 'green', 'أخضر': 'green',
    'بني': 'brown', 'رمادي': 'gray', 'اصفر': 'yellow', 'أصفر': 'yellow',
    'شاي لبن': 'beige/milk-tea', 'سماوي': 'sky blue', 'كحلي': 'navy',
}

# (raw arabic term, make, model, is_body_type)
MODEL_MAP = [
    (r'اي\s*تن\s*قراند|اي\s*تن\s*جراند|i10\s*grand', 'Hyundai', 'i10 Grand'),
    (r'اي\s*تونتي\s*i20\s*grand', 'Hyundai', 'i20 Grand'),
    (r'اي\s*تونتي\s*i20|i20', 'Hyundai', 'i20'),
    (r'اي\s*تن\s*جياد', 'Hyundai', 'i10'),
    (r'اي\s*تن|i10', 'Hyundai', 'i10'),
    (r'كوريلا|كورولا|كورلا|كريلا', 'Toyota', 'Corolla'),
    (r'سحلية|منفوخة', 'Hyundai', 'Avante (Elantra)'),
    (r'افانتي|النترا', 'Hyundai', 'Elantra/Avante'),
    (r'دبدوب', 'Hyundai', 'Accent (old-gen, دبدوب)'),
    (r'فيرنا|نيو\s*فيرنا', 'Hyundai', 'Accent (Verna)'),
    (r'اكسنت\s*جياد|اكسنت\s*نيو\s*فيرنا|اكسنت', 'Hyundai', 'Accent'),
    (r'كيلك|قيتز', 'Hyundai', 'Getz'),
    (r'اتوز\s*برايم|اتوز', 'Hyundai', 'Atos'),
    (r'مورنيق|مورنينق|بيكانتو', 'Kia', 'Picanto/Morning'),
    (r'توسان', 'Hyundai', 'Tucson'),
    (r'سنتافي', 'Hyundai', 'Santa Fe'),
    (r'سوناتا', 'Hyundai', 'Sonata'),
    (r'استايركس|ستايركس', 'Hyundai', 'Starex'),
    (r'اتش\s*ون\s*h1|هايس\s*اتش\s*ون', 'Hyundai', 'H1'),
    (r'كيا\s*ريو', 'Kia', 'Rio'),
    (r'كيا\s*سيراتو', 'Kia', 'Cerato'),
    (r'اوبتما|k5', 'Kia', 'Optima/K5'),
    (r'سبورتاج|اسبورتاج', 'Kia', 'Sportage'),
    (r'كيا\s*k3', 'Kia', 'K3'),
    (r'ايون\s*eon|ايون', 'Hyundai', 'Eon'),
    (r'جيلي', 'Geely', None),
    (r'بي\s*واي\s*دي\s*byd\s*f3|byd\s*f3', 'BYD', 'F3'),
    (r'بي\s*واي\s*دي\s*byd\s*l3|byd\s*l3', 'BYD', 'L3'),
    (r'بي\s*واي\s*دي\s*byd\s*s7|byd\s*s7', 'BYD', 'S7'),
    (r'شفروليت\s*سبارك', 'Chevrolet', 'Spark'),
    (r'شفروليت\s*افيو', 'Chevrolet', 'Aveo'),
    (r'شفروليت', 'Chevrolet', None),
    (r'نوبيرا', 'Daewoo', 'Nubira'),
    (r'كامري', 'Toyota', 'Camry'),
    (r'برادو', 'Toyota', 'Land Cruiser Prado'),
    (r'لاندكروز(ر)?', 'Toyota', 'Land Cruiser'),
    (r'باجيرو', 'Mitsubishi', 'Pajero'),
    (r'لانسر\s*بومة|لانسر\s*قرش|لانسر|بومه|بومة', 'Mitsubishi', 'Lancer'),
    (r'سكودا\s*فابيا', 'Skoda', 'Fabia'),
    (r'سكودا', 'Skoda', None),
    (r'نيسان\s*صني', 'Nissan', 'Sunny'),
    (r'نيسان\s*اكس\s*ترايل', 'Nissan', 'X-Trail'),
    (r'نيسان\s*يورفان', 'Nissan', 'Urvan'),
    (r'يارس', 'Toyota', 'Yaris'),
    (r'راف\s*فور|rav4', 'Toyota', 'RAV4'),
    (r'كريسيدا', 'Toyota', 'Cressida'),
    (r'ليكزس', 'Lexus', None),
    (r'اودي\s*audi|اودي', 'Audi', None),
    (r'فلكسواجن|فلوكسواجن', 'Volkswagen', None),
    (r'رينو\s*داستر', 'Renault', 'Duster'),
    (r'رينو', 'Renault', None),
    (r'مارسيدس|ماريسدس', 'Mercedes-Benz', None),
    (r'ام\s*جي\s*mg|ام\s*جي', 'MG', None),
    (r'هوندا\s*سيتي|هوندا\s*ستي', 'Honda', 'City'),
    (r'سوزوكي\s*التو\s*alto|سوزوكي\s*التو|التو\s*k10|سوزوكي.*k10', 'Suzuki', 'Alto'),
    (r'سوزوكي', 'Suzuki', None),
    (r'همر', 'Hummer', None),
    (r'لافيتا', 'Daewoo', 'Lafeeta'),
    (r'تريكان', 'Hyundai', 'Trajet'),
    (r'تيروس', 'Mitsubishi', 'Triton (تيروس)'),
    (r'هايس\s*ميتسوبيشي|هايس\s*كريز|كريز', 'Mitsubishi', 'L300 (كريز/هايس ميتسوبيشي)'),
    (r'هايس\s*تايوتا|هايس\s*صيني|هايس', 'Toyota', 'Hiace'),
    (r'ميتسوبيشي\s*l300', 'Mitsubishi', 'L300'),
    (r'ميتسوبيشي', 'Mitsubishi', None),
    (r'جيب\s*jeep|جيب', 'Jeep', None),
    (r'تايوتا\s*تاتشر', 'Toyota', 'Dyna (تاتشر)'),
    (r'بورتر', 'Hyundai', 'Porter'),
    (r'داف\s*daf|داف', 'DAF', None),
    (r'حافلة\s*كوستر', 'Toyota', 'Coaster'),
    (r'حافلة\s*نيسان', 'Nissan', 'bus (حافلة)'),
]

BODY_TYPE_TERMS = ['مضلع', 'شريحة', 'شريحه', 'بوكس', 'بوكسي', 'حافلة', 'باص', 'لوري',
                    'قلاب', 'ركشة', 'تكتك', 'توك توك', 'دقو ورا', 'امجاد', 'دفار',
                    'صالون', 'عائلية', 'عربة عائلية', 'مقطورة', 'راس شاحنة', 'شاحنة',
                    'تركتر', 'تنكر']

def map_model(raw):
    low = raw.strip()
    for pat, make, model in MODEL_MAP:
        if re.search(pat, low):
            return make, model
    return None, None

def classify_body(raw):
    for t in BODY_TYPE_TERMS:
        if t in raw:
            return t
    return None

def parse_line(num, rest, sublist_id):
    orig = rest
    color = None
    m = re.search(r'\(([^)]{1,20})\)', rest)
    if m:
        color = m.group(1).strip()
        rest_wo_color = rest[:m.start()] + rest[m.end():]
    else:
        rest_wo_color = rest

    chassis = None
    mchassis = re.search(r'شاسي\s*([0-9A-Za-z]+)', rest_wo_color)
    if mchassis:
        chassis = mchassis.group(1).strip()
        model_part = rest_wo_color[:mchassis.start()].strip()
        after = rest_wo_color[mchassis.end():]
    else:
        model_part = rest_wo_color.strip()
        after = ''
        # sometimes no شاسي keyword but a bare number appears at the end (treat as chassis)
        mnum = re.search(r'^(.*?)(\d{4,})\s*$', model_part)
        if mnum and 'لوحة' not in model_part:
            model_part2 = mnum.group(1).strip()
            if model_part2:
                chassis = mnum.group(2)
                model_part = model_part2

    plate = None
    mplate = re.search(r'لوحة\s*([0-9]+)\s*([؀-ۿ0-9\s]{0,6})', after if after else rest_wo_color)
    if mplate:
        area = mplate.group(2).strip()
        plate = (mplate.group(1) + (' ' + area if area else '')).strip()

    model_part = model_part.strip(' /-')
    if model_part in ('', 'See less'):
        model_part = None

    make, model = (None, None)
    if model_part:
        make, model = map_model(model_part)
    body = classify_body(model_part) if model_part else None

    if not model and body:
        model = f"{body} (body type, raw: {model_part})"
    elif not model and model_part:
        model = None  # ambiguous -> leave null per rule 7/8
    color_en = COLOR_MAP.get(color, color) if color else None

    desc = f"from post text kashf list {sublist_id} item {num}; raw label: '{orig.strip()}'"
    return {
        "make": make, "model": model, "color": color_en, "year": None,
        "chassis": chassis, "plate": plate, "status": "unknown",
        "location": None, "contact": None, "description": desc
    }

def main(path, post_id):
    d = json.load(open(path, encoding='utf-8'))
    post = next(p for p in d if p['id'] == post_id)
    text = post['text']
    vehicles = []
    # split into sublists by the كشف markers
    parts = re.split(r'\[.*?\]\s*زولي شديد:\s*كشف\s*(?:مشترك)?\s*\(?(A\d+)\)?', text)
    # parts[0] is header, then alternating (label, body)
    header = parts[0]
    for i in range(1, len(parts), 2):
        label = parts[i]
        body = parts[i+1] if i+1 < len(parts) else ''
        for line in body.split('\n'):
            line = line.strip()
            if not line:
                continue
            mm = re.match(r'^(\d{1,3})/\s*(.*)$', line)
            if not mm:
                continue
            num, rest = mm.group(1), mm.group(2)
            if not rest.strip():
                continue
            v = parse_line(num, rest, label)
            vehicles.append(v)
    return header, vehicles

if __name__ == '__main__':
    header, vehicles = main(sys.argv[1], int(sys.argv[2]))
    out = {"header_and_contacts": header, "count": len(vehicles), "vehicles": vehicles}
    outpath = sys.argv[3] if len(sys.argv) > 3 else None
    if outpath:
        with open(outpath, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False)
    else:
        sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode('utf-8'))
