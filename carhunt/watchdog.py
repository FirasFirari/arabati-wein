"""Restart services that are running but not working, catch up missed jobs,
and tell the owner.

The .bat loops already handle a crashed process. This handles the quieter
failures:

* a service alive while its connection is dead (Telethon retries forever) --
  judged by heartbeat freshness, restarted;
* a scheduled JOB that has not succeeded for too long -- a trigger missed while
  paused, asleep or off -- started again with `schtasks /Run` (2026-11-02: a
  54-hour pause left the daily lane three days behind and nothing noticed);
* the owner never hearing about any of it: until 2026-11-04 every alert went
  to `telegram.alert_chat`, which is "me", and returned without sending.
  Alerts now use notify.send_telegram (`telegram.notify_chat_id`).

Every cycle also pings the dead-man checks (carhunt/deadman.py), so a PC that
is off, asleep or fully paused is reported from OUTSIDE, by Healthchecks.io.

Usage:
    python -m carhunt.watchdog          # one check
    python -m carhunt.watchdog --loop   # keep checking every 5 minutes
"""
import argparse
import datetime
import json
import subprocess
import time

from . import deadman, health
from .config import DATA_DIR

LOG = DATA_DIR / "logs" / "watchdog.log"
STALE_AFTER = 300  # seconds without a heartbeat before we act

SERVICES = {
    "channel_monitor": "CarHunt Channel Monitor",
    "intake_bot": "CarHunt Intake Bot",
    "fb_service": "CarHunt Facebook Service",
}

# job stamp name -> (scheduled task, hours without a success before catch-up)
JOBS = {
    "daily_free_lane": ("CarHunt Daily Free Lane", 26),
    "raw_sweep": ("CarHunt Raw Sweep", 3),
    "photo_ocr": ("CarHunt Photo OCR", 6),
    "clarity": ("CarHunt Clarity Snapshot", 30),
    "backup": ("CarHunt Backup", 30),
}
CATCHUP_STATE = health.HEALTH_DIR / "catchup.json"
CATCHUP_RETRY_HOURS = 2
LIGHT_PAUSE_REMIND_HOURS = 12

_unhealthy_last_cycle: set[str] = set()


def log(message: str) -> None:
    # print only: the launcher .bat redirects stdout to the log file, and
    # opening the same file here would fight it for the Windows lock
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def alert(text: str) -> None:
    try:
        from . import notify
        if not notify.send_telegram(text):
            log("alert NOT delivered (see notify log)")
    except Exception as e:
        log(f"could not send alert: {e}")


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


def paused() -> bool:
    return (DATA_DIR / "PAUSED").exists()


def check_services() -> list[str]:
    """Restart unhealthy services; return their names."""
    unhealthy = []
    for name, task in SERVICES.items():
        # scripts/carhunt_control.ps1 pause [-Light]: a stopped service is
        # deliberate, not stuck — restarting it would undo the pause.
        if paused() or (name == "fb_service" and (DATA_DIR / "PAUSED_LIGHT").exists()):
            log(f"{name}: paused by the owner — left alone")
            continue
        age = health.age_seconds(name)
        if age is None:
            log(f"{name}: no heartbeat file yet — restarting")
        elif age > STALE_AFTER:
            log(f"{name}: last healthy {age / 60:.1f} min ago — restarting")
        else:
            log(f"{name}: healthy ({age:.0f}s ago)")
            continue
        restart(task)
        unhealthy.append(name)
    return unhealthy


def _load_state() -> dict:
    try:
        return json.loads(CATCHUP_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    try:
        CATCHUP_STATE.parent.mkdir(parents=True, exist_ok=True)
        CATCHUP_STATE.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def catch_up(now: float | None = None, run=None) -> list[str]:
    """Start any job whose last SUCCESS is older than its limit.

    A job that has never written a stamp is left alone (no evidence either
    way). A disabled task makes `schtasks /Run` fail, which is correct: paused
    means paused. One attempt per job per CATCHUP_RETRY_HOURS.
    """
    if paused():
        return []
    now = time.time() if now is None else now
    run = run or (lambda task: subprocess.run(["schtasks", "/Run", "/TN", task],
                                              capture_output=True).returncode)
    state = _load_state()
    started = []
    for job, (task, max_hours) in JOBS.items():
        age = health.job_age_seconds(job)
        if age is None or age < max_hours * 3600:
            continue
        if now - state.get(job, 0) < CATCHUP_RETRY_HOURS * 3600:
            continue
        rc = run(task)
        state[job] = now
        log(f"catch-up: {job} last succeeded {age / 3600:.1f} h ago — "
            f"started {task!r} (schtasks exit {rc})")
        started.append(job)
    if started:
        _save_state(state)
    return started


def remind_light_pause(now: float | None = None) -> bool:
    """A light pause stops collection and scoring but not this watchdog, so a
    forgotten one is invisible from outside. Remind every 12 hours."""
    flag = DATA_DIR / "PAUSED_LIGHT"
    if not flag.exists():
        return False
    now = time.time() if now is None else now
    hours = (now - flag.stat().st_mtime) / 3600
    state = _load_state()
    if hours < LIGHT_PAUSE_REMIND_HOURS or now - state.get("_light_reminded", 0) < 12 * 3600:
        return False
    alert(f"⏸ Car Hunt has been in LIGHT pause for {hours:.0f} h: Facebook collection and "
          f"scoring are stopped. Resume with the 'CarHunt Resume' desktop shortcut.")
    state["_light_reminded"] = now
    _save_state(state)
    return True


def check_once(alerts: bool = True) -> list[str]:
    """One watchdog cycle. Returns the services found unhealthy."""
    global _unhealthy_last_cycle
    if paused():
        log("paused (data/PAUSED) — nothing checked")
        return []
    deadman.ping("carhunt-alive")
    unhealthy = check_services()
    if unhealthy and alerts:
        alert("⚠️ CarHunt watchdog restarted: " + ", ".join(unhealthy))
    # a service still unhealthy after a restart is broken, not hiccuping
    persistent = sorted(set(unhealthy) & _unhealthy_last_cycle)
    if persistent:
        deadman.ping("services", False, "unhealthy after restart: " + ", ".join(persistent))
    elif not unhealthy:
        deadman.ping("services")
    _unhealthy_last_cycle = set(unhealthy)
    started = catch_up()
    if started and alerts:
        alert("🔁 CarHunt catch-up: started " + ", ".join(started)
              + " (no successful run for too long — missed while paused, asleep or off?)")
    remind_light_pause()
    return unhealthy


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
