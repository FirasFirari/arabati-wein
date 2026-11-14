"""Write the standing-watch intake form as an XLSForm for KoboToolbox / ODK.

The site's watch form needs a browser and a connection at the moment someone
finds nothing. A volunteer at a recovery committee's desk or a community
meeting has neither. This is the same registration as an XLSForm, so a partner
can collect watches offline in KoboCollect and send them in bulk.

It asks for the same three things as the site (identifier, contact, optional
description), adds the state the vehicle was taken from as an OCHA P-code so
answers join to the boundary data without cleaning, and carries the same
consent and scam warning. Arabic first, English second.

    python scripts\\build_kobo_form.py     # writes open-data/kobo/watch_intake.xlsx

Needs openpyxl. Check the result with `python -m pyxform.xls2xform` (pyxform).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from openpyxl import Workbook

from carhunt import geo
from carhunt.config import PROJECT_ROOT

OUT = PROJECT_ROOT / "open-data" / "kobo" / "watch_intake.xlsx"
AR, EN = "Arabic (ar)", "English (en)"

SURVEY_COLS = ["type", "name", f"label::{AR}", f"label::{EN}", f"hint::{AR}", f"hint::{EN}",
               "required", "relevant", "constraint", f"constraint_message::{AR}",
               f"constraint_message::{EN}", "appearance"]

# Arabic-Indic and ASCII digits both count: people type what their keyboard gives them.
DIGIT = "[0-9٠-٩]"
CHASSIS_RULE = f"regex(., '^[A-Za-z0-9٠-٩ -]*({DIGIT}[A-Za-z ٠-٩0-9-]*){{4,}}$')"
PLATE_RULE = f"regex(., '{DIGIT}{{3,}}')"
PHONE_RULE = f"regex(., '^[+]?({DIGIT}[ -]?){{9,14}}$')"


def row(**kw) -> list:
    return [kw.get(c, "") for c in SURVEY_COLS]


def survey_rows() -> list[list]:
    L = lambda ar, en: {f"label::{AR}": ar, f"label::{EN}": en}
    H = lambda ar, en: {f"hint::{AR}": ar, f"hint::{EN}": en}
    M = lambda ar, en: {f"constraint_message::{AR}": ar, f"constraint_message::{EN}": en}
    return [
        row(type="start", name="start"),
        row(type="end", name="end"),
        row(type="today", name="today"),
        row(type="note", name="intro", **L(
            "تسجيل رقم للمتابعة في سجل «عربتي وين؟». التسجيل مجاني. إذا ظهر الرقم في "
            "أي كشف أو منشور جديد نبلّغ صاحبه. لا تدفع لأي شخص مقابل معلومات: "
            "الجهات الرسمية مجانية.",
            "Register a number to watch in the Arabati Wein registry. It is free. If the "
            "number appears in a new list or post, the registrant is told. Never pay "
            "anyone for information: the official channels are free.")),
        row(type="select_one id_type", name="id_type", required="yes", **L(
            "ما الرقم المتوفر؟", "Which number do you have?")),
        row(type="text", name="chassis", required="yes",
            relevant="${id_type} = 'chassis' or ${id_type} = 'both'",
            constraint=CHASSIS_RULE,
            **L("رقم الشاسي", "Chassis number"),
            **H("كاملاً أو آخر الأرقام كما في شهادة البحث. أربعة أرقام على الأقل.",
                "In full, or the last digits as on the registration. At least four digits."),
            **M("أدخل أربعة أرقام على الأقل.", "Enter at least four digits.")),
        row(type="text", name="plate", required="yes",
            relevant="${id_type} = 'plate' or ${id_type} = 'both'",
            constraint=PLATE_RULE,
            **L("رقم اللوحة", "Plate number"),
            **H("مع الحرف ورقم المنطقة، مثال: 12345 خ 1",
                "With the letter and region digit, for example 12345 خ 1"),
            **M("أدخل رقم اللوحة.", "Enter the plate number.")),
        row(type="text", name="description",
            **L("وصف العربة (اختياري)", "Vehicle description (optional)"),
            **H("الماركة، الموديل، اللون، السنة", "Make, model, colour, year")),
        row(type="select_one adm1", name="taken_adm1", appearance="minimal",
            **L("الولاية التي فُقدت فيها العربة (اختياري)",
                "State where the vehicle was taken (optional)")),
        row(type="text", name="contact", required="yes", constraint=PHONE_RULE,
            appearance="numbers",
            **L("رقم للتواصل مع صاحب العربة", "A number to reach the owner"),
            **H("واتساب أو هاتف، مع مفتاح الدولة إن كان خارج السودان",
                "WhatsApp or phone, with the country code if outside Sudan"),
            **M("أدخل رقم هاتف صحيحاً.", "Enter a valid phone number.")),
        row(type="select_one channel", name="contact_channel", required="yes",
            **L("كيف نتواصل؟", "How should we reach them?")),
        row(type="acknowledge", name="consent", required="yes", **L(
            "أوافق على حفظ هذه البيانات لغرض التنبيه فقط. لن تُنشر ولن تُشارك مع أي طرف آخر.",
            "I agree to this being stored only to send an alert. It is never published "
            "and never shared.")),
        row(type="note", name="safety", **L(
            "لن نطلب رقم الشاسي في رسالة خاصة، ولن نطلب رمز تحقق، ولن نطلب مالاً. "
            "البلاغ الرسمي المجاني: sudanpolice.net/reg/car_stealing",
            "We never ask for a chassis number in a private message, never ask for a "
            "verification code and never ask for money. The free official report: "
            "sudanpolice.net/reg/car_stealing")),
    ]


def choice_rows() -> list[list]:
    rows = [
        ["id_type", "chassis", "رقم الشاسي", "Chassis number"],
        ["id_type", "plate", "رقم اللوحة", "Plate number"],
        ["id_type", "both", "الاثنان", "Both"],
        ["channel", "whatsapp", "واتساب", "WhatsApp"],
        ["channel", "phone", "اتصال هاتفي", "Phone call"],
        ["channel", "telegram", "تيليجرام", "Telegram"],
    ]
    # the choice NAME is the P-code, so the answer is already the join key
    rows += [["adm1", code, ar, en] for code, (en, ar) in sorted(geo.ADMIN1.items())]
    rows.append(["adm1", "outside_sudan", "خارج السودان", "Outside Sudan"])
    rows.append(["adm1", "unknown", "غير معروف", "Not known"])
    return rows


def main() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "survey"
    ws.append(SURVEY_COLS)
    for r in survey_rows():
        ws.append(r)
    ch = wb.create_sheet("choices")
    ch.append(["list_name", "name", f"label::{AR}", f"label::{EN}"])
    for r in choice_rows():
        ch.append(r)
    st = wb.create_sheet("settings")
    st.append(["form_title", "form_id", "version", "default_language", "style"])
    st.append(["Arabati Wein: standing watch intake", "arabati_wein_watch_intake", "1", AR, "pages"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"written: {OUT.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
