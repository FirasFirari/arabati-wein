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


# ---- periodic jobs ---------------------------------------------------------
# Services beat while connected; scheduled JOBS stamp when they start and when
# they finish. The watchdog reads the finish stamps to catch up a job that has
# not succeeded for too long (a trigger missed while paused, asleep or off),
# and the dashboard can show "last ran" for each. Files: data/health/job_<name>.json

def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _job_path(name: str):
    return HEALTH_DIR / f"job_{name}.json"


def job_read(name: str) -> dict:
    try:
        return json.loads(_job_path(name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _job_write(name: str, data: dict) -> None:
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _job_path(name).with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(_job_path(name))


def job_start(name: str) -> None:
    try:
        data = job_read(name)
        data["last_start"] = _now()
        _job_write(name, data)
    except Exception:
        pass


def job_done(name: str, exit_code: int = 0, note: str = "") -> None:
    """Record the end of a run; `last_ok` moves only on exit code 0."""
    try:
        data = job_read(name)
        data.update(last_end=_now(), last_exit=int(exit_code), note=note[:200])
        if exit_code == 0:
            data["last_ok"] = data["last_end"]
        _job_write(name, data)
    except Exception:
        pass


def job_age_seconds(name: str, key: str = "last_ok") -> float | None:
    """Seconds since the job last succeeded (or started, key='last_start')."""
    value = job_read(name).get(key)
    if not value:
        return None
    try:
        then = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return (datetime.datetime.now(datetime.timezone.utc) - then).total_seconds()
