"""Intake bot: family/friends forward suspicious posts to this Telegram bot,
which analyzes them and replies with a verdict.

Usage:
    python -m carhunt.intake_bot
"""
import asyncio
import sys
import time

from telethon import TelegramClient, events

from . import db, health
from .config import MEDIA_DIR, load_config
from .pipeline import analyze_post, make_analyzer

REPLY_TEMPLATE = """\
Score: {score}/100
{reasons}

النتيجة: {score}/100 — {arabic_hint}"""


QUEUED_REPLY = """\
Received and saved, thank you. It was checked at once for the car's chassis
and plate; every number in it is read in the daily review.

وصل، شكراً. تم حفظ المنشور وفُحص فوراً بحثاً عن رقم الشاسي واللوحة المطلوبين،
وتُقرأ كل الأرقام فيه ضمن المراجعة اليومية."""


def _arabic_hint(score: int | None) -> str:
    if score is None:
        return "حصل خطأ في التحليل، حاول مرة أخرى"
    if score >= 70:
        return "⚠️ تطابق قوي! سيتم التحقق فوراً — لا تتواصل مع البائع"
    if score >= 40:
        return "تطابق محتمل، سيتم مراجعته"
    return "غالباً ليست السيارة المطلوبة، شكراً للمساهمة"


_RATE: dict[int, list[float]] = {}


def _allowed(cfg: dict, sender_id: int) -> bool:
    """Only trusted senders may spend API budget.

    An empty allowlist means open access, which is what you want while the bot
    is being shared with family — set telegram.allowed_user_ids to lock it.
    """
    allowed = (cfg["telegram"].get("allowed_user_ids") or [])
    return not allowed or sender_id in allowed


def _within_rate_limit(cfg: dict, sender_id: int) -> bool:
    """Cap submissions per sender per hour so one person cannot drain budget."""
    limit = int(cfg["telegram"].get("max_per_user_per_hour", 30))
    now = time.time()
    hits = [t for t in _RATE.get(sender_id, []) if now - t < 3600]
    if len(hits) >= limit:
        _RATE[sender_id] = hits
        return False
    hits.append(now)
    _RATE[sender_id] = hits
    return True


async def handle(client: TelegramClient, analyzer, cfg: dict,
                 messages: list) -> None:
    first = messages[0]
    sender = await first.get_sender()
    sender_id = getattr(sender, "id", None)
    sender_name = getattr(sender, "username", None) or getattr(sender, "first_name", "?")

    # gate BEFORE downloading media or calling any model
    if sender_id is None or not _allowed(cfg, sender_id):
        print(f"blocked submission from {sender_name} ({sender_id})")
        await first.reply("هذا البوت مخصص لمجموعة محددة. "
                          "This bot is limited to an approved group.")
        return
    if not _within_rate_limit(cfg, sender_id):
        await first.reply("وصلت الحد الأقصى للإرسال هذه الساعة، حاول لاحقاً. "
                          "Hourly limit reached — please try later.")
        return

    source_ref = f"intake:{first.chat_id}:{first.id}"
    if db.has_post(source_ref):
        return

    text = "\n".join(m.message for m in messages if m.message) or None
    media_paths = []
    for m in messages:
        if m.photo:
            path = await m.download_media(file=str(MEDIA_DIR) + "/")
            if path:
                media_paths.append(path)

    if not text and not media_paths:
        await first.reply("Send a post's text and/or photos. أرسل نص المنشور أو صوره.")
        return

    post_id = db.add_post(f"intake:{sender_name}", source_ref, None, text, media_paths)
    if post_id is None:
        return

    verdict = await asyncio.to_thread(
        analyze_post, analyzer, cfg, post_id, text, media_paths)
    score = verdict.get("score")
    print(f"[{source_ref}] from {sender_name}: score={score}")

    if verdict.get("tier") == "queued":
        await first.reply(QUEUED_REPLY)
    else:
        await first.reply(REPLY_TEMPLATE.format(
            score=score if score is not None else "?",
            reasons=verdict.get("reasons", verdict.get("error", "")),
            arabic_hint=_arabic_hint(score),
        ))

    if score is not None and score >= cfg["matching"]["alert_threshold"]:
        alert = (f"🚨 Intake match from {sender_name} — score {score}/100\n"
                 f"{verdict.get('reasons', '')}\n{text or ''}")
        # notify_chat_id is the operator chat the bot can reach; alert_chat
        # ("me") is unreachable for a bot and silently dropped every alert.
        from . import notify
        if not await asyncio.to_thread(notify.send_telegram, alert):
            print("Alert delivery failed; verdict is stored in the database.")


async def main() -> None:
    cfg = load_config()
    tg = cfg["telegram"]
    if not tg.get("bot_token"):
        sys.exit("Set telegram.bot_token in config.yaml (create a bot via @BotFather)")

    db.init()
    analyzer = make_analyzer(cfg)       # None unless live_scoring.enabled (paid)
    bot = TelegramClient("carhunt_bot", tg["api_id"], tg["api_hash"])
    await bot.start(bot_token=tg["bot_token"])

    @bot.on(events.Album())
    async def on_album(event):
        await handle(bot, analyzer, cfg, list(event.messages))

    @bot.on(events.NewMessage(incoming=True))
    async def on_message(event):
        if event.message.grouped_id:
            return
        await handle(bot, analyzer, cfg, [event.message])

    async def heartbeat():
        """Report health only while genuinely connected to Telegram."""
        while True:
            if bot.is_connected():
                health.beat("intake_bot")
            await asyncio.sleep(60)

    print("Intake bot running. Ctrl+C to stop.")
    asyncio.create_task(heartbeat())
    await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
