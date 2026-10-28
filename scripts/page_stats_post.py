"""Print a ready-to-paste Arabic Facebook post with the live registry numbers.

    python scripts\\page_stats_post.py            # from site/static/stats.json
    python scripts\\page_stats_post.py --en       # English variant

Numbers come from stats.json (written by build_deploy.py / publish_stats.py), so
the post can never claim figures the site does not show. Paste the BODY as the
post and the LINK as the first comment (facebook_posts.md rule: no URL in the body).
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATS = ROOT / "site" / "static" / "stats.json"
AR_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def ar(n: int) -> str:
    return f"{n:,}".translate(AR_DIGITS).replace(",", "٬")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    s = json.loads(STATS.read_text(encoding="utf-8"))
    en = "--en" in sys.argv
    tag = "stats"
    link = f"https://mycarsd.online/?utm_source=facebook&utm_medium=page&utm_campaign={tag}"
    if en:
        body = (
            f"Registry update. {s['identified_vehicles']:,} vehicles with a chassis or plate "
            f"number are now searchable, drawn from {s['posts_collected']:,} public posts in "
            f"Sudanese lost-vehicle groups and channels. {s['standing_watches']:,} families have a "
            f"standing watch on their number.\n\n"
            "Search is free, needs no account, and works on a phone. If your number is not there, "
            "register a watch and we tell you when it appears.\n\n"
            "Official recovery is free: file at sudanpolice.net and attend the Traffic Department in "
            "person. There is no official WhatsApp number. Never pay anyone to release a car."
        )
    else:
        body = (
            f"تحديث السجل: {ar(s['identified_vehicles'])} عربة برقم شاسي أو لوحة أصبحت قابلة للبحث، "
            f"مجموعة من {ar(s['posts_collected'])} منشور عام في مجموعات وقنوات العربات المفقودة. "
            f"و{ar(s['standing_watches'])} أسرة سجّلت تنبيهاً على رقمها.\n\n"
            "البحث مجاني، بلا حساب، ويعمل من الموبايل. اكتب رقم الشاسي أو اللوحة، ويكفي جزء من الرقم. "
            "وإن لم تجد رقمك سجّل تنبيهاً ونخبرك إن ظهر.\n\n"
            "الاسترداد الرسمي مجاني: بلّغ في sudanpolice.net وراجع إدارة المرور بنفسك. "
            "لا يوجد رقم واتساب رسمي، ولا تدفع لأحد مقابل «الإفراج» عن عربتك."
        )
    print("=== BODY (post) ===\n" + body + "\n\n=== FIRST COMMENT ===\n" + link +
          f"\n\n(stats generated {s['generated_at']})")


if __name__ == "__main__":
    main()
