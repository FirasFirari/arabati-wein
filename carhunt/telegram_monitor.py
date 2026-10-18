"""Monitor Telegram channels/groups for posts that may show the car.

Usage:
    python -m carhunt.telegram_monitor              # live monitoring
    python -m carhunt.telegram_monitor --backfill 300   # also scan the last N
                                                        # messages of each channel
"""
import argparse
import asyncio
import sys

from telethon import TelegramClient, events

from . import db, health
from .analyzer import Analyzer
from .config import MEDIA_DIR, load_config
from .pipeline import analyze_post


def _alert_text(url: str | None, verdict: dict) -> str:
    lines = [
        f"🚨 Possible match — score {verdict.get('score')}/100",
        f"Reasons: {verdict.get('reasons', '?')}",
    ]
    extracted = verdict.get("extracted") or {}
    for key in ("described_vehicle", "plate", "location", "price", "contact"):
        if extracted.get(key):
            lines.append(f"{key}: {extracted[key]}")
    if url:
        lines.append(url)
    return "\n".join(lines)


async def process_messages(client: TelegramClient, analyzer: Analyzer, cfg: dict,
                           source: str, messages: list) -> None:
    """Store + analyze one logical post (a single message or a whole album)."""
    first = messages[0]
    source_ref = f"tg:{source}:{first.id}"
    if db.has_post(source_ref):
        return

    text = "\n".join(m.message for m in messages if m.message) or None
    media_paths = []
    for m in messages:
        if m.photo:
            try:
                path = await m.download_media(file=str(MEDIA_DIR) + "/")
            except Exception as e:  # expired file references on old posts, etc.
                print(f"  media download failed ({source}:{m.id}): {e}")
                continue
            if path:
                media_paths.append(path)

    username = getattr(first.chat, "username", None)
    url = f"https://t.me/{username}/{first.id}" if username else None
    post_id = db.add_post(f"telegram:{source}", source_ref, url, text, media_paths)
    if post_id is None:
        return

    verdict = await asyncio.to_thread(
        analyze_post, analyzer, cfg, post_id, text, media_paths)
    score = verdict.get("score")
    print(f"[{source_ref}] score={score} {verdict.get('reasons', verdict.get('error', ''))}")

    if score is not None and score >= cfg["matching"]["alert_threshold"]:
        await client.send_message(cfg["telegram"]["alert_chat"], _alert_text(url, verdict))


async def backfill(client: TelegramClient, analyzer: Analyzer, cfg: dict,
                   channel, limit: int) -> None:
    name = getattr(channel, "username", None) or str(channel.id)
    print(f"Backfilling last {limit} messages of {name}...")
    albums: dict[int, list] = {}
    singles = []
    async for m in client.iter_messages(channel, limit=limit):
        if m.grouped_id:
            albums.setdefault(m.grouped_id, []).append(m)
        else:
            singles.append(m)
    for group in albums.values():
        group.sort(key=lambda m: m.id)
        await process_messages(client, analyzer, cfg, name, group)
    for m in singles:
        await process_messages(client, analyzer, cfg, name, [m])


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", type=int, default=0,
                        help="also analyze the last N messages per channel")
    parser.add_argument("--backfill-only", action="store_true",
                        help="exit after the backfill instead of live monitoring")
    args = parser.parse_args()

    cfg = load_config()
    tg = cfg["telegram"]
    if not tg["channels"]:
        sys.exit("No channels configured — add them under telegram.channels in config.yaml")
    if not tg["api_id"] or not tg["api_hash"]:
        sys.exit("Set telegram.api_id and telegram.api_hash in config.yaml "
                 "(from https://my.telegram.org)")

    db.init()
    analyzer = Analyzer(cfg)
    client = TelegramClient(tg["session_name"], tg["api_id"], tg["api_hash"])
    await client.start()

    channels = [await client.get_entity(c) for c in tg["channels"]]

    # live updates only arrive for channels the account has joined
    from telethon.tl.functions.channels import JoinChannelRequest
    for channel in channels:
        try:
            await client(JoinChannelRequest(channel))
        except Exception as e:
            print(f"join {getattr(channel, 'username', channel.id)}: {e}")

    @client.on(events.Album(chats=channels))
    async def on_album(event):
        name = getattr(event.chat, "username", None) or str(event.chat_id)
        await process_messages(client, analyzer, cfg, name, list(event.messages))

    @client.on(events.NewMessage(chats=channels))
    async def on_message(event):
        if event.message.grouped_id:  # albums are handled by the Album event
            return
        name = getattr(event.chat, "username", None) or str(event.chat_id)
        await process_messages(client, analyzer, cfg, name, [event.message])

    if args.backfill:
        for channel in channels:
            await backfill(client, analyzer, cfg, channel, args.backfill)

    if args.backfill_only:
        print("Backfill complete, exiting.")
        await client.disconnect()
        return

    async def heartbeat():
        """Report health only while genuinely connected to Telegram."""
        while True:
            if client.is_connected():
                health.beat("channel_monitor",
                            f"{len(channels)} channels")
            await asyncio.sleep(60)

    print(f"Live monitoring {len(channels)} channel(s). Ctrl+C to stop.")
    asyncio.create_task(heartbeat())
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
