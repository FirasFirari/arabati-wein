"""Outbound notifications for standing-watch leads and target hits.

Until 2026-10-18 the only signal that a watched number had appeared was a
Windows toast on the operator's PC. This module gives the hourly raw sweep three
real channels, none of which can crash the sweep (every public function
swallows its own errors and logs to data/logs/notify.log):

  * Telegram to the OPERATOR via the Bot HTTP API (`telegram.bot_token` +
    `telegram.notify_chat_id`, a numeric chat id — a bot cannot message "me").
  * Email to a REGISTRANT via Resend, if their contact is an email address and
    a key exists (`RESEND_API_KEY` or site/resend_key.txt). Otherwise a logged
    no-op, so the code path is ready the day the domain is verified.
  * WhatsApp for a REGISTRANT whose contact is a phone number: no free API can
    send a WhatsApp message, so a `wa.me` link with the message pre-written is
    appended to data/leads_outbox.md and included in the operator's Telegram.
    One tap on a phone opens the chat with the text in place.

Safeguarding rules baked into every message (see docs/SAFEGUARDING.md):
the registrant is told WHERE the number appeared and sent to the official free
channels; they are told never to contact the poster and never to pay; the
poster's text is NEVER quoted, because it may carry the poster's phone number.

    python -m carhunt.notify --whoami            # chat ids seen by the bot
    python -m carhunt.notify --test "hello"      # send to notify_chat_id
    python -m carhunt.notify --simulate IDENT POST_ID   # compose, don't send
    python -m carhunt.notify --outbox            # print pending wa.me drafts
"""
import datetime
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from .config import DATA_DIR, PROJECT_ROOT, load_config

LOG = DATA_DIR / "logs" / "notify.log"
OUTBOX = DATA_DIR / "leads_outbox.md"
RESEND_KEY_FILE = PROJECT_ROOT / "site" / "resend_key.txt"
SITE = "https://mycarsd.online"
POLICE = "https://sudanpolice.net/reg/car_stealing"

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# country codes a registrant of a Sudanese registry plausibly writes
_CC = ("249", "20", "966", "971", "974", "965", "973", "968", "218", "235", "211", "251", "254")


def log(msg: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


def _cfg() -> dict:
    try:
        return load_config() or {}
    except Exception as e:
        log(f"config unreadable: {e}")
        return {}


def _tg() -> dict:
    return _cfg().get("telegram") or {}


# ---------------------------------------------------------------- Telegram --
def send_telegram(text: str, chat_id=None) -> bool:
    """Send `text` to the operator's chat. False (and a log line) on any failure."""
    tg = _tg()
    token = tg.get("bot_token")
    chat = chat_id or tg.get("notify_chat_id")
    if not token or not chat:
        log("telegram skipped: bot_token or notify_chat_id missing")
        return False
    body = json.dumps({"chat_id": chat, "text": text[:4000],
                       "disable_web_page_preview": True}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            ok = json.loads(r.read().decode("utf-8")).get("ok", False)
        log(f"telegram {'sent' if ok else 'rejected'} ({len(text)} chars)")
        return bool(ok)
    except urllib.error.HTTPError as e:
        log(f"telegram HTTP {e.code}: {e.read()[:200]!r}")
    except Exception as e:
        log(f"telegram failed: {e}")
    return False


def whoami() -> list[tuple[int, str]]:
    """(chat id, name) for every chat that has messaged the bot recently.

    Empty while another process (the intake bot) is consuming updates with the
    same token, or if a webhook is set — pause the intake bot for a minute.
    """
    token = _tg().get("bot_token")
    if not token:
        return []
    try:
        with urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/getUpdates", timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        log(f"getUpdates failed: {e}")
        return []
    seen, out = set(), []
    for u in data.get("result", []):
        msg = u.get("message") or u.get("edited_message") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is not None and cid not in seen:
            seen.add(cid)
            name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name"),
                                        chat.get("title"), chat.get("username")) if x)
            out.append((cid, name))
    return out


# ------------------------------------------------------------------ phones --
def normalize_phone(raw: str):
    """E.164 digits (no plus) for a contact, or None if it is not a phone.

    Sudan local 09x/01x (10 digits) -> 249…; Egypt 01x (11 digits) -> 20…;
    Saudi 05x (10 digits) -> 966…; numbers already carrying a country code pass
    through. Anything else is left for a human.
    """
    s = (raw or "").translate(_AR_DIGITS)
    s = re.sub(r"[^\d+]", "", s)
    if not s:
        return None
    if s.startswith("+"):
        s = s[1:]
    elif s.startswith("00"):
        s = s[2:]
    if not s.isdigit():
        return None
    if re.fullmatch(r"0[19]\d{8}", s):
        return "249" + s[1:]
    if re.fullmatch(r"01[0125]\d{8}", s):
        return "20" + s[1:]
    if re.fullmatch(r"05\d{8}", s):
        return "966" + s[1:]
    if 10 <= len(s) <= 13 and s.startswith(_CC):
        return s
    return None


def is_email(contact: str) -> bool:
    return bool(_EMAIL.match((contact or "").strip()))


def whatsapp_link(phone: str, text: str):
    digits = normalize_phone(phone)
    if not digits:
        return None
    return f"https://wa.me/{digits}?text={urllib.parse.quote(text, safe='')}"


# ------------------------------------------------------------------- email --
def _resend_key():
    key = os.environ.get("RESEND_API_KEY")
    if key:
        return key.strip()
    try:
        if RESEND_KEY_FILE.exists():
            return RESEND_KEY_FILE.read_text(encoding="utf-8").strip() or None
    except Exception:
        pass
    return None


def send_email(to: str, subject: str, text: str) -> bool:
    key = _resend_key()
    if not key:
        log(f"email stub (no Resend key): would send to {to[:3]}… subject {subject!r}")
        return False
    sender = ((_cfg().get("notify") or {}).get("resend_from")
              or "Arabati Wein <alerts@mycarsd.online>")
    body = json.dumps({"from": sender, "to": [to], "subject": subject,
                       "text": text}).encode("utf-8")
    req = urllib.request.Request("https://api.resend.com/emails", data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read()
        log(f"email sent to {to[:3]}…")
        return True
    except urllib.error.HTTPError as e:
        log(f"email HTTP {e.code}: {e.read()[:200]!r}")
    except Exception as e:
        log(f"email failed: {e}")
    return False


# ---------------------------------------------------------------- messages --
def _source_label(source: str, lang: str) -> str:
    source = source or ""
    if source.startswith("facebook:"):
        gid = source.split(":", 1)[1]
        name = ""
        for g in ((_cfg().get("facebook") or {}).get("groups") or []):
            if str(g.get("id")) == gid:
                name = (g.get("name") or "").split("(")[0].strip()
        if lang == "ar":
            return f"مجموعة فيسبوك {name}".strip() if name else "مجموعة على فيسبوك"
        return f"the Facebook group {name}".strip() if name else "a Facebook group"
    if source.startswith("telegram:"):
        ch = source.split(":", 1)[1]
        return f"قناة تيليجرام {ch}" if lang == "ar" else f"the Telegram channel {ch}"
    return "منشور عام" if lang == "ar" else "a public post"


def _date(seen_at: str) -> str:
    return (seen_at or "")[:10]


def compose_lead(watch: dict, hit: dict, post: dict) -> str:
    """The message a registrant receives. Arabic first, English after.

    Tells them where the number appeared and what to do officially. Never
    quotes the post text (it may carry the poster's phone), never suggests
    contacting the poster, never asks for money or for the chassis number.
    """
    ident = watch.get("identifier", "")
    url = post.get("url") or ""
    kind = hit.get("kind") or ""
    src_ar, src_en = _source_label(post.get("source"), "ar"), _source_label(post.get("source"), "en")
    date = _date(post.get("seen_at"))
    kind_ar = {"found list": "في قائمة عربات موجودة أو محجوزة",
               "lost report": "في بلاغ فقدان (قد يكون بلاغك أنت)"}.get(kind, "")
    kind_en = {"found list": "in a list of found or impounded vehicles",
               "lost report": "in a lost-vehicle report (it may be your own post)"}.get(kind, "")
    link_ar = (f"رابط المنشور: {url}" if url else
               "لا يوجد رابط محفوظ لهذا المنشور؛ ابحث عن الرقم داخل المجموعة نفسها.")
    link_en = (f"Post link: {url}" if url else
               "No saved link for this post; search for the number inside the group itself.")
    return (
        f"السلام عليكم. هذه رسالة من «عربتي وين؟» ({SITE}).\n"
        f"الرقم الذي سجّلته للمتابعة ({ident}) ظهر {kind_ar} في منشور عام على {src_ar}"
        f"{' بتاريخ ' + date if date else ''}.\n{link_ar}\n"
        f"هذا تنبيه آلي وليس تأكيداً — تحقّق من المنشور بنفسك.\n"
        f"مهم: لا تتواصل مع صاحب المنشور مباشرة ولا تدفع أي مبلغ لأي شخص. "
        f"الاسترداد الرسمي مجاني: بلّغ على {POLICE} ثم اذهب بنفسك إلى إدارة المرور "
        f"أو فرعية مكافحة سرقة السيارات. لا يوجد رقم واتساب رسمي.\n"
        f"— عربتي وين؟\n\n"
        f"Hello. This is Arabati Wein ({SITE}). The number you registered ({ident}) "
        f"appeared {kind_en} in a public post on {src_en}{' on ' + date if date else ''}.\n"
        f"{link_en}\nThis is an automatic alert, not a confirmation; check the post yourself.\n"
        f"Important: do not contact the poster directly and never pay anyone. Official recovery "
        f"is free: file at {POLICE} and go in person to the Traffic Department or the "
        f"vehicle-theft branch. There is no official WhatsApp number."
    )


def _operator_text(watch: dict, hit: dict, post: dict, channel: str, link) -> str:
    url = post.get("url") or "no saved link"
    lines = [f"CarHunt lead: watch {watch.get('identifier')}",
             f"post {hit.get('post_id')} · {hit.get('kind') or 'unclear'} · {_date(post.get('seen_at'))}",
             f"why: {hit.get('why')}",
             f"source: {post.get('source')}",
             f"url: {url}",
             f"registrant contact type: {channel}"]
    if link:
        lines.append(f"one-tap WhatsApp draft: {link}")
    elif channel == "email":
        lines.append("email sent (or stubbed — see notify.log)")
    else:
        lines.append("no usable contact — see data/leads_outbox.md")
    lines.append("Verify by eye before anything else. Never contact the poster.")
    return "\n".join(lines)


def _append_outbox(watch: dict, hit: dict, post: dict, link, message: str) -> None:
    OUTBOX.parent.mkdir(parents=True, exist_ok=True)
    with OUTBOX.open("a", encoding="utf-8") as fh:
        fh.write(f"\n## {datetime.datetime.now():%Y-%m-%d %H:%M} · watch {watch.get('identifier')} · post {hit.get('post_id')}\n\n")
        fh.write(f"- kind: {hit.get('kind')} · why: {hit.get('why')}\n")
        fh.write(f"- post: {post.get('url') or 'no saved link'} ({post.get('source')})\n")
        fh.write(f"- registered {watch.get('created_at', '')[:10]}; note: {watch.get('note') or '-'}\n")
        if link:
            fh.write(f"- **Tap to open WhatsApp with the message ready:** {link}\n")
        else:
            fh.write(f"- contact could not be parsed as a phone or email: `{watch.get('contact')}`\n")
        fh.write("\n<details><summary>message text</summary>\n\n" + message + "\n\n</details>\n")


def notify_watch_hit(watch: dict, hit: dict, conn) -> dict:
    """Fan one new raw-sweep hit out to the operator and the registrant."""
    result = {"telegram": False, "channel": "none", "outbox": False, "email": False}
    try:
        row = conn.execute("SELECT url, text, source, seen_at FROM posts WHERE id = ?",
                           (hit.get("post_id"),)).fetchone()
        post = {"url": row[0], "text": row[1], "source": row[2], "seen_at": row[3]} if row else {}
        message = compose_lead(watch, hit, post)
        contact = (watch.get("contact") or "").strip()
        link = None
        if is_email(contact):
            result["channel"] = "email"
            result["email"] = send_email(
                contact, "عربتي وين؟ — ظهر الرقم الذي سجّلته / Arabati Wein: your number appeared",
                message)
        else:
            link = whatsapp_link(contact, message)
            result["channel"] = "whatsapp" if link else "none"
        _append_outbox(watch, hit, post, link, message)
        result["outbox"] = True
        result["telegram"] = send_telegram(_operator_text(watch, hit, post, result["channel"], link))
        log(f"lead {watch.get('identifier')} post {hit.get('post_id')}: {result}")
    except Exception as e:
        log(f"notify_watch_hit failed: {e}")
    return result


def notify_target_hits(count: int) -> bool:
    return send_telegram(f"CarHunt: TARGET identifier found in {count} new post(s). "
                         f"Open data\\raw_sweep_NEW.txt and verify by eye before anything else.")


# --------------------------------------------------------------------- CLI --
def _main(argv) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--whoami", action="store_true")
    ap.add_argument("--test", metavar="TEXT")
    ap.add_argument("--simulate", nargs=2, metavar=("IDENT", "POST_ID"))
    ap.add_argument("--outbox", action="store_true")
    a = ap.parse_args(argv)
    if a.whoami:
        chats = whoami()
        if not chats:
            print("no chats seen. Message the bot first, and pause the intake bot for a "
                  "minute (it consumes the same updates). Then rerun.")
            return 1
        for cid, name in chats:
            print(f"chat id {cid}  {name}")
        print("\nput the id into config.yaml -> telegram.notify_chat_id")
        return 0
    if a.test:
        ok = send_telegram(a.test)
        print("sent" if ok else "NOT sent — see data/logs/notify.log")
        return 0 if ok else 1
    if a.simulate:
        from . import db, watches as watchlib
        ident, pid = a.simulate[0], int(a.simulate[1])
        watch = next((w for w in watchlib.load_watches() if w["identifier"] == ident),
                     {"identifier": ident, "contact": "0912345678", "note": "(simulated)"})
        conn = db.connect()
        row = conn.execute("SELECT url, text, source, seen_at FROM posts WHERE id = ?", (pid,)).fetchone()
        post = {"url": row[0], "text": row[1], "source": row[2], "seen_at": row[3]} if row else {}
        hit = {"post_id": pid, "why": "simulated", "kind": "found list"}
        msg = compose_lead(watch, hit, post)
        print(msg)
        print("\n--- operator ---")
        print(_operator_text(watch, hit, post, "whatsapp", whatsapp_link(watch["contact"], msg)))
        return 0
    if a.outbox:
        print(OUTBOX.read_text(encoding="utf-8") if OUTBOX.exists() else "outbox empty")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
