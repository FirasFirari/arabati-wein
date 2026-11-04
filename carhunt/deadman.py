"""Dead-man pings to Healthchecks.io: an alert when a job STOPS happening.

Why: on 2026-11-02 everything was paused and stayed paused for 54 hours, and
the daily lane had been dying on "database is locked" for two weeks, and
nobody was told about either. A watchdog on this PC cannot report that the PC
itself is off, asleep or paused; an outside service that expects a ping and
alerts when it does not arrive can. Healthchecks.io (free plan) alerts on
Telegram.

Configuration (config.yaml, private):

    healthchecks:
      ping_key: "<project ping key>"   # Project settings -> Ping key

Checks are addressed by slug: https://hc-ping.com/<ping_key>/<slug>. With no
key configured every call is a no-op.

Rules this module keeps:
* It NEVER raises and never holds a job for more than ~15 s (a background
  thread with a bounded join; DNS hangs on Windows ignore urlopen's timeout).
* Ping bodies carry counts, exit codes and exception class names ONLY. Never a
  log tail: those hold registrants' chassis numbers and post URLs.
"""
from __future__ import annotations

import contextlib
import threading
import time
import urllib.request

_KEY: str | None = None

# The checks configured on Healthchecks.io (slug -> what it watches).
CHECKS = {
    "carhunt-alive": "watchdog loop; DOWN = PC off, asleep or PAUSED",
    "services": "the three collectors are healthy",
    "raw-sweep": "hourly raw sweep finished",
    "daily-lane": "daily free lane finished with every step OK",
    "gemini-scoring": "both Gemini scoring steps exited 0",
    "photo-ocr": "photo OCR sweep finished",
    "clarity": "Clarity snapshot finished",
    "backup-nightly": "nightly verified DB backup",
    "backup-offsite": "weekly encrypted offsite copy",
}


def _key() -> str:
    global _KEY
    if _KEY is None:
        try:
            from .config import load_config
            _KEY = str(((load_config() or {}).get("healthchecks") or {}).get("ping_key") or "")
        except Exception:
            _KEY = ""
    return _KEY


def _send(url: str, data: bytes | None) -> None:
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                         headers={"User-Agent": "carhunt-deadman"})
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
            return
        except Exception:
            time.sleep(2 * (attempt + 1))


def url_for(slug: str, ok=True, start: bool = False, key: str | None = None) -> str:
    key = _key() if key is None else key
    url = f"https://hc-ping.com/{key}/{slug}"
    if start:
        return url + "/start"
    if ok is False:
        return url + "/fail"
    if isinstance(ok, int) and not isinstance(ok, bool):
        return url + f"/{ok}"
    return url


def ping(slug: str, ok=True, body: str = "", *, start: bool = False, wait: float = 15.0) -> None:
    """ok=True -> success, False -> /fail, an int -> /<exit code>, start -> /start."""
    try:
        if not _key():
            return
        data = body.encode("utf-8", "ignore")[:20_000] if body else None
        t = threading.Thread(target=_send, args=(url_for(slug, ok, start), data), daemon=True)
        t.start()
        t.join(wait)
    except Exception:
        pass  # a monitoring failure must never become a job failure


@contextlib.contextmanager
def tracked(job: str, slug: str):
    """Stamp data/health/job_<job>.json and ping <slug> around a job's run.

        with deadman.tracked("raw_sweep", "raw-sweep"):
            main()

    The exit code is the job's own: 0 on normal return or sys.exit(0), the
    SystemExit code otherwise, 1 on an exception (which is re-raised).
    """
    from . import health
    health.job_start(job)
    ping(slug, start=True)
    rc = 1
    try:
        yield
        rc = 0
    except SystemExit as e:
        rc = 0 if e.code in (None, 0) else (e.code if isinstance(e.code, int) else 1)
        raise
    finally:
        health.job_done(job, rc)
        ping(slug, rc)
