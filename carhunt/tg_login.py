"""One-time Telethon login via QR code (no interactive terminal needed).

Usage:
    python -m carhunt.tg_login
Generates data/tg_qr.png — scan it from the Telegram phone app:
Settings > Devices > Add Device. Refreshes the QR until scanned.
"""
import asyncio

import qrcode
from telethon import TelegramClient
from telethon.errors import AuthTokenExpiredError, SessionPasswordNeededError

from .config import DATA_DIR, load_config

QR_PATH = DATA_DIR / "tg_qr.png"


async def main() -> None:
    tg = load_config()["telegram"]
    client = TelegramClient(tg["session_name"], tg["api_id"], tg["api_hash"])
    await client.connect()
    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"Already logged in as {me.first_name} — nothing to do.")
        return

    qr = await client.qr_login()
    print("QR login started.")
    for attempt in range(20):  # QR tokens expire ~30s; keep refreshing
        qrcode.make(qr.url).save(QR_PATH)
        print(f"QR saved to {QR_PATH} (attempt {attempt + 1}) — scan with "
              "Telegram app: Settings > Devices > Add Device", flush=True)
        try:
            await qr.wait(25)
            break
        except SessionPasswordNeededError:
            print("This account has a 2FA cloud password — run interactively "
                  "in your own terminal instead: python -m carhunt.telegram_monitor")
            return
        except (asyncio.TimeoutError, AuthTokenExpiredError):
            # expired before or during a scan — mint a new token and go again
            try:
                await qr.recreate()
            except Exception:
                qr = await client.qr_login()
    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"Logged in as {me.first_name}. Session saved — the monitor can now run.")
    else:
        print("QR was not scanned in time. Run again when ready.")


if __name__ == "__main__":
    asyncio.run(main())
