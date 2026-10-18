"""Liveness heartbeats for the long-running services.

A process being alive is not the same as a process working: after a network
outage Telethon retries forever, so the service stays "running" while
receiving nothing. Each service therefore writes a heartbeat ONLY while it is
actually connected, and the watchdog judges freshness rather than existence.
"""
import datetime
import json

from .config import DATA_DIR

HEALTH_DIR = DATA_DIR / "health"


def beat(name: str, detail: str = "") -> None:
    """Record that `name` is connected and working, right now."""
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "detail": detail,
    }
    (HEALTH_DIR / f"{name}.json").write_text(
        json.dumps(payload), encoding="utf-8")


def age_seconds(name: str) -> float | None:
    """Seconds since `name` last reported healthy, or None if never."""
    path = HEALTH_DIR / f"{name}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        then = datetime.datetime.fromisoformat(data["at"])
    except (json.JSONDecodeError, KeyError, ValueError):
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - then).total_seconds()


def read(name: str) -> dict | None:
    path = HEALTH_DIR / f"{name}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
