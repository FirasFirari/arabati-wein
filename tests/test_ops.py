"""Watchdog catch-up, the light-pause reminder, and dead-man pings.

2026-11-02: a pause ran 54 hours unnoticed and the daily lane fell three days
behind; for two weeks before that the lane had been crashing on database locks.
Nobody was told about either. These tests pin the rules that now tell them.
"""
import json
import os
import time

import pytest

from carhunt import deadman, health, watchdog


@pytest.fixture()
def hdir(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "HEALTH_DIR", tmp_path / "health")
    monkeypatch.setattr(watchdog, "DATA_DIR", tmp_path)
    monkeypatch.setattr(watchdog, "CATCHUP_STATE", tmp_path / "health" / "catchup.json")
    sent = []
    monkeypatch.setattr(watchdog, "alert", sent.append)
    return tmp_path, sent


def stamp(job, hours_ago):
    health.job_start(job)
    health.job_done(job, 0)
    data = health.job_read(job)
    t = time.time() - hours_ago * 3600
    import datetime
    data["last_ok"] = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()
    health._job_write(job, data)


def test_job_stamps_only_move_last_ok_on_success(hdir):
    health.job_start("x")
    health.job_done("x", 0)
    ok = health.job_read("x")["last_ok"]
    health.job_done("x", 1)
    assert health.job_read("x")["last_ok"] == ok
    assert health.job_read("x")["last_exit"] == 1


def test_stale_job_is_started_once(hdir):
    stamp("daily_free_lane", 30)          # limit is 26 h
    stamp("raw_sweep", 1)                 # fresh
    ran = []
    assert watchdog.catch_up(run=lambda task: ran.append(task) or 0) == ["daily_free_lane"]
    assert ran == ["CarHunt Daily Free Lane"]
    # the next cycle (5 min later) must not start it again
    assert watchdog.catch_up(run=lambda task: ran.append(task) or 0) == []


def test_never_stamped_job_is_left_alone(hdir):
    assert watchdog.catch_up(run=lambda task: 0) == []


def test_nothing_is_caught_up_while_paused(hdir):
    tmp, _ = hdir
    stamp("daily_free_lane", 30)
    (tmp / "PAUSED").write_text("")
    assert watchdog.catch_up(run=lambda task: 0) == []


def test_light_pause_reminder_after_12_hours_then_every_12(hdir):
    tmp, sent = hdir
    flag = tmp / "PAUSED_LIGHT"
    flag.write_text("")
    old = time.time() - 13 * 3600
    os.utime(flag, (old, old))
    assert watchdog.remind_light_pause() is True
    assert sent and "LIGHT pause" in sent[0]
    assert watchdog.remind_light_pause() is False      # not again for 12 h


def test_fresh_light_pause_is_not_nagged(hdir):
    tmp, sent = hdir
    (tmp / "PAUSED_LIGHT").write_text("")
    assert watchdog.remind_light_pause() is False and not sent


def test_ping_url_shapes():
    assert deadman.url_for("raw-sweep", key="K") == "https://hc-ping.com/K/raw-sweep"
    assert deadman.url_for("raw-sweep", start=True, key="K").endswith("/raw-sweep/start")
    assert deadman.url_for("raw-sweep", False, key="K").endswith("/raw-sweep/fail")
    assert deadman.url_for("raw-sweep", 3, key="K").endswith("/raw-sweep/3")
    assert deadman.url_for("raw-sweep", 0, key="K").endswith("/raw-sweep/0")


def test_ping_without_a_key_is_a_silent_no_op(monkeypatch):
    monkeypatch.setattr(deadman, "_KEY", "")
    deadman.ping("raw-sweep")       # must not raise or block


def test_tracked_records_the_jobs_own_exit_code(hdir, monkeypatch):
    pings = []
    monkeypatch.setattr(deadman, "ping", lambda slug, ok=True, body="", start=False, **k:
                        pings.append((slug, "start" if start else ok)))
    with deadman.tracked("raw_sweep", "raw-sweep"):
        pass
    with pytest.raises(SystemExit):
        with deadman.tracked("raw_sweep", "raw-sweep"):
            raise SystemExit(3)
    with pytest.raises(ValueError):
        with deadman.tracked("raw_sweep", "raw-sweep"):
            raise ValueError("boom")
    assert pings == [("raw-sweep", "start"), ("raw-sweep", 0), ("raw-sweep", "start"),
                     ("raw-sweep", 3), ("raw-sweep", "start"), ("raw-sweep", 1)]
    assert json.loads((health.HEALTH_DIR / "job_raw_sweep.json").read_text())["last_exit"] == 1


def test_a_restart_is_silent_until_the_service_stays_down(hdir, monkeypatch):
    """Owner's rule 2026-11-16: the system deals with it; speak only when stuck."""
    tmp, sent = hdir
    monkeypatch.setattr(watchdog, "_unhealthy_streak", {})
    monkeypatch.setattr(watchdog, "check_services", lambda: ["intake_bot"])
    monkeypatch.setattr(watchdog, "catch_up", lambda: [])
    pings = []
    monkeypatch.setattr(deadman, "ping", lambda slug, ok=True, *a, **k: pings.append((slug, ok)))
    for _ in range(watchdog.PERSISTENT_CYCLES - 1):
        watchdog.check_once()
    assert sent == []                                   # hiccups: nobody is told
    assert ("services", False) in pings                 # but the dashboard shows it
    for _ in range(10):
        watchdog.check_once()
    assert len(sent) == 1 and "intake_bot" in sent[0]   # told once, not every cycle
    monkeypatch.setattr(watchdog, "check_services", lambda: [])
    watchdog.check_once()
    assert watchdog._unhealthy_streak == {} and pings[-1] == ("services", True)


def test_catch_up_is_silent_until_it_has_failed_three_times(hdir, monkeypatch):
    tmp, sent = hdir
    stamp("daily_free_lane", 30)
    now = time.time()
    step = watchdog.CATCHUP_RETRY_HOURS * 3600 + 1
    for i in range(watchdog.GIVE_UP_AFTER):
        assert watchdog.catch_up(now=now + i * step, run=lambda task: 0) == ["daily_free_lane"]
        assert watchdog.given_up() == []                # three attempts: still silent
    watchdog.catch_up(now=now + 3 * step, run=lambda task: 0)   # a fourth was needed
    assert watchdog.given_up() == ["daily_free_lane"]
    watchdog.catch_up(now=now + 4 * step, run=lambda task: 0)
    assert watchdog.given_up() == []                    # reported once
    stamp("daily_free_lane", 1)                         # it finally ran
    watchdog.catch_up(now=now + 5 * step, run=lambda task: 0)
    state = json.loads(watchdog.CATCHUP_STATE.read_text())
    assert "_tries_daily_free_lane" not in state and "_gave_up_daily_free_lane" not in state


def test_healthchecks_policy_one_last_resort_everything_else_silent():
    from carhunt import hc
    channels = [{"id": "E", "kind": "email"}, {"id": "T", "kind": "telegram"}]
    existing = [
        {"uuid": "1", "slug": "carhunt-alive", "channels": "E,T", "grace": 21600},
        {"uuid": "2", "slug": "raw-sweep", "channels": "E,T", "grace": 10800},
        {"uuid": "3", "slug": "clarity", "channels": "", "grace": 43200},
    ]
    calls = hc.plan(existing, channels)
    assert ("POST", "/checks/1", {"channels": "T", "grace": 48 * 3600}) in calls
    assert ("POST", "/checks/2", {"channels": ""}) in calls
    assert not [c for c in calls if c[1] == "/checks/3"]          # already silent
    created = [c[2]["slug"] for c in calls if c[1] == "/checks/"]
    assert created == ["search-console"]
    # idempotent: nothing to do once the rule holds
    done = [{"uuid": "1", "slug": "carhunt-alive", "channels": "T", "grace": 48 * 3600},
            {"uuid": "4", "slug": "search-console", "channels": "", "grace": 43200}]
    assert hc.plan(done, channels) == []


def test_pause_silences_only_what_the_pause_stops():
    from carhunt import hc
    listing = {"checks": [
        {"uuid": "1", "slug": "carhunt-alive", "status": "up"},
        {"uuid": "2", "slug": "daily-lane", "status": "down"},
        {"uuid": "3", "slug": "photo-ocr", "status": "paused"},
        {"uuid": "4", "slug": "backup-offsite", "status": "new"}]}
    posted = []
    api = lambda m, p, b=None: listing if m == "GET" else posted.append(p)
    assert hc.pause(light=True, api=api) == ["daily-lane"]
    assert posted == ["/checks/2/pause"]
    posted.clear()
    assert hc.pause(api=api) == ["carhunt-alive", "daily-lane"]   # paused/new need nothing
