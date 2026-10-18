"""Restart services that are running but not actually working.

The .bat loops already handle a crashed process. This handles the quieter
failure: a process that stays alive while its connection is dead — which is
what happened during the network outage, when Telethon retried forever and
nothing noticed the intake bot had stopped receiving messages.

Usage:
    python -m carhunt.watchdog          # one check
    python -m carhunt.watchdog --loop   # keep checking every 5 minutes
"""
import argparse
import datetime
import subprocess
import time

from . import health
from .config import DATA_DIR, load_config

LOG = DATA_DIR / "logs" / "watchdog.log"
STALE_AFTER = 300  # seconds without a heartbeat before we act

SERVICES = {
    "channel_monitor": "CarHunt Channel Monitor",
    "intake_bot": "CarHunt Intake Bot",
    "fb_service": "CarHunt Facebook Service",
}


def log(message: str) -> None:
    # print only: the launcher .bat redirects stdout to the log file, and
    # opening the same file here would fight it for the Windows lock
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def restart(task_name: str) -> None:
    """Kill the stuck python process, then let the scheduled task relaunch."""
    module = {
        "CarHunt Channel Monitor": "telegram_monitor",
        "CarHunt Intake Bot": "intake_bot",
        "CarHunt Facebook Service": "fb_service",
    }[task_name]
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "
         f"'*{module}*' "
         "} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
         "-ErrorAction SilentlyContinue }"],
        capture_output=True)
    time.sleep(3)
    subprocess.run(["schtasks", "/Run", "/TN", task_name], capture_output=True)


def check_once(alert: bool = True) -> list[str]:
    """Return the names of services found unhealthy (and restart them)."""
    unhealthy = []
    for name, task in SERVICES.items():
        # scripts/carhunt_control.ps1 pause [-Light]: a stopped service is
        # deliberate, not stuck — restarting it would undo the pause.
        if (DATA_DIR / "PAUSED").exists() or (
                name == "fb_service" and (DATA_DIR / "PAUSED_LIGHT").exists()):
            log(f"{name}: paused by the owner — left alone")
            continue
        age = health.age_seconds(name)
        if age is None:
            log(f"{name}: no heartbeat file yet — restarting")
            restart(task)
            unhealthy.append(name)
        elif age > STALE_AFTER:
            log(f"{name}: last healthy {age / 60:.1f} min ago — restarting")
            restart(task)
            unhealthy.append(name)
        else:
            log(f"{name}: healthy ({age:.0f}s ago)")
    if unhealthy and alert:
        notify(unhealthy)
    return unhealthy


def notify(unhealthy: list[str]) -> None:
    """Tell the owner over Telegram that a service had to be restarted."""
    try:
        cfg = load_config()
        token = cfg["telegram"].get("bot_token")
        chat = cfg["telegram"].get("alert_chat")
        if not token or not chat or chat == "me":
            return  # a bot cannot message "me"; needs a numeric user id
        import urllib.parse
        import urllib.request
        text = ("⚠️ CarHunt watchdog restarted: " + ", ".join(unhealthy))
        url = (f"https://api.telegram.org/bot{token}/sendMessage?"
               + urllib.parse.urlencode({"chat_id": chat, "text": text}))
        urllib.request.urlopen(url, timeout=15).read()
    except Exception as e:
        log(f"could not send alert: {e}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
    args = parser.parse_args()
    if not args.loop:
        check_once()
        return
    while True:
        try:
            check_once()
        except Exception as e:
            log(f"watchdog error: {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
