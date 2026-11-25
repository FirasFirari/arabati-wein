"""The watch verifier remembers its leads, and stays quiet about repeats.

What these pin (2026-11-25, after one handled lead produced 12 Telegram cards):

* a new COPY of a list entry already judged a lead is not read again and sends
  nothing, but stays a visible match (it is never written as "not a sighting");
* a DIFFERENT list for a watch that already has a live lead is still read and
  still reported, as a line in the summary, not a second card;
* a recovered watch is never verified again;
* two runs cannot work one queue, and hits queued during a run survive it.

Synthetic numbers and text only: this file is public.
"""
import json
import os

import pytest

from carhunt import db, verify
from carhunt import watches as watchlib

IDENT = "731942"
LIST_A = "قائمة موجودات رقم 5\nتوسان 118204\nاكسنت مضلع 731942\nكليك 220815\n"
LIST_B = "حصر جديد بولاية اخرى\n7- اكسنت مضلع رمادي شاسي 731942 موديل 2015\n"


@pytest.fixture()
def env(tmp_path, monkeypatch, test_config):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "carhunt.db")
    monkeypatch.setattr(db, "LOCK_LOG", tmp_path / "locks.log")
    monkeypatch.setattr(db, "GATE_LOG", tmp_path / "gate.log")
    monkeypatch.setattr(db, "_CFG", test_config)
    for name in ("QUEUE", "LOG", "VERDICTS", "LEADS", "LOCK"):
        monkeypatch.setattr(verify, name, tmp_path / f"verify_{name.lower()}")
    monkeypatch.setattr(watchlib, "REVIEWED_JSON", tmp_path / "watch_reviewed.json")
    monkeypatch.setattr(watchlib, "CLOSED_JSON", tmp_path / "watch_closed.json")
    monkeypatch.setattr(verify, "_cfg", lambda: {"model": "test", "timeout_s": 5,
                                                 "max_groups_per_run": 12, "min_confidence": 0.6})
    out = {"telegram": [], "cards": [], "asked": []}
    monkeypatch.setattr(verify.notify, "send_telegram", lambda text: out["telegram"].append(text) or True)
    monkeypatch.setattr(verify.notify, "notify_watch_hit",
                        lambda watch, hit, conn, **kw: out["cards"].append(hit["post_id"]) or {})
    db.init()
    return tmp_path, out


def post(n, text):
    return db.add_post("telegram:@x", f"tg:@x:{n}", f"https://t.me/x/{n}", text, [])


def hit(pid):
    return (IDENT, {"post_id": pid, "why": "raw text: exact chassis", "kind": "found list"})


def answer(verdict, conf=0.8, out=None):
    def ask(ev, cfg):
        if out is not None:
            out["asked"].append(ev["post"]["id"])
        return {"verdict": verdict, "confidence": conf, "reason": "test", "model": "test"}
    return ask


def run(out, verdict="possible sighting"):
    con = db.connect()
    try:
        return verify.run(con, [{"identifier": IDENT, "note": "اكسنت مضلع", "contact": ""}],
                          ask=answer(verdict, out=out))
    finally:
        con.close()


def test_first_sighting_sends_one_card_and_is_remembered(env):
    _, out = env
    a = post(1, LIST_A)
    verify.enqueue([hit(a)])
    stats = run(out)
    assert stats["leads"] == 1 and out["cards"] == [a] and len(out["telegram"]) == 1
    assert [e["post_ids"] for e in verify.load_leads()] == [[a]]
    assert verify.load_queue() == []


def test_copy_of_a_known_lead_is_silent_and_stays_a_match(env):
    _, out = env
    a = post(1, LIST_A)
    verify.enqueue([hit(a)])
    run(out)
    out["asked"].clear()
    copy = post(2, "Shared post\n" + LIST_A)
    verify.enqueue([hit(copy)])
    stats = run(out)
    assert stats["known"] == 1 and stats["leads"] == 0
    assert out["asked"] == []                         # not read again
    assert out["cards"] == [a] and len(out["telegram"]) == 1      # nothing new sent
    assert (IDENT, copy) not in watchlib.load_reviewed()          # still a visible match
    assert verify.load_leads()[0]["post_ids"] == [a, copy]
    assert verify.load_queue() == []


def test_a_different_list_is_still_read_and_reported_without_a_second_card(env):
    _, out = env
    a = post(1, LIST_A)
    verify.enqueue([hit(a)])
    run(out)
    b = post(2, LIST_B)
    verify.enqueue([hit(b)])
    stats = run(out)
    assert out["asked"] == [a, b]                     # the new source WAS read
    assert stats["leads"] == 1
    assert out["cards"] == [a]                        # no second card
    assert len(out["telegram"]) == 2 and "NEW SOURCE" in out["telegram"][1]
    assert "https://t.me/x/2" in out["telegram"][1]   # the operator can open it
    assert len(verify.load_leads()) == 2


def test_two_lists_in_one_run_give_one_card_and_one_line(env):
    _, out = env
    a, b = post(1, LIST_A), post(2, LIST_B)
    verify.enqueue([hit(a), hit(b)])
    run(out)
    assert out["cards"] == [a] and len(out["telegram"]) == 1
    assert "NEW SOURCE" in out["telegram"][0]


def test_a_new_source_judged_a_different_car_is_still_filed(env):
    _, out = env
    a = post(1, LIST_A)
    verify.enqueue([hit(a)])
    run(out)
    b = post(2, "لوحات\nبوكسي لوحة 731942 خ1\n")
    verify.enqueue([hit(b)])
    run(out, verdict="different car")
    assert (IDENT, b) in watchlib.load_reviewed()


def test_a_lead_killed_by_hand_no_longer_counts_and_its_copies_follow_it(env):
    tmp, out = env
    a = post(1, LIST_A)
    verify.enqueue([hit(a)])
    run(out)
    # the registrant answers: not my car
    (tmp / "watch_reviewed.json").write_text(json.dumps([
        {"identifier": IDENT, "post_id": a, "verdict": "different car",
         "reviewed_by": "user (registrant reply)"}]), encoding="utf-8")
    out["asked"].clear()
    copy, other = post(2, "Shared\n" + LIST_A), post(3, LIST_B)
    verify.enqueue([hit(copy), hit(other)])
    stats = run(out)
    assert (IDENT, copy) in watchlib.load_reviewed()  # same entry: same verdict, no call
    assert out["asked"] == [other]
    assert out["cards"] == [a, other]                 # a dead lead is not a "known lead": full card
    assert stats["known"] == 0


def test_hand_marked_lead_stops_copies_being_auto_dismissed(env):
    _, out = env
    a = post(1, LIST_A)
    con = db.connect()
    verify.mark_lead(con, IDENT, a, "raw text: exact chassis")
    con.close()
    copy = post(2, "Shared\n" + LIST_A)
    verify.enqueue([hit(copy)])
    stats = run(out, verdict="different car")         # what the verifier WOULD have said
    assert stats["known"] == 1 and out["asked"] == []
    assert (IDENT, copy) not in watchlib.load_reviewed()


def test_recovered_watch_is_never_verified(env):
    tmp, out = env
    (tmp / "watch_closed.json").write_text(json.dumps(
        [{"identifier": IDENT, "status": "recovered", "date": "2026-01-01"}]), encoding="utf-8")
    a = post(1, LIST_A)
    verify.enqueue([hit(a)])
    stats = run(out)
    assert out["asked"] == [] and out["cards"] == [] and out["telegram"] == []
    assert stats["groups"] == 0 and verify.load_queue() == []


def test_second_run_backs_off_and_leaves_the_queue(env):
    _, out = env
    a = post(1, LIST_A)
    verify.enqueue([hit(a)])
    verify.LOCK.write_text(f"{os.getpid()} now", encoding="utf-8")   # a live run holds it
    stats = run(out)
    assert stats.get("locked") == 1 and out["asked"] == []
    assert len(verify.load_queue()) == 1
    verify.LOCK.unlink()
    assert run(out)["leads"] == 1


def test_lock_of_a_dead_process_is_taken_over(env):
    _, out = env
    a = post(1, LIST_A)
    verify.enqueue([hit(a)])
    verify.LOCK.write_text("0 long ago", encoding="utf-8")
    assert run(out)["leads"] == 1
    assert not verify.LOCK.exists()


def test_hits_queued_during_a_run_are_not_lost(env):
    _, out = env
    a, b = post(1, LIST_A), post(2, LIST_B)
    verify.enqueue([hit(a)])

    def ask(ev, cfg):                                 # another process queues mid-run
        verify.enqueue([hit(b)])
        return {"verdict": "possible sighting", "confidence": 0.8, "reason": "t", "model": "test"}

    con = db.connect()
    verify.run(con, [{"identifier": IDENT, "note": "", "contact": ""}], ask=ask)
    con.close()
    assert [i["post_id"] for i in verify.load_queue()] == [b]


def test_failed_call_is_kept_and_counted(env):
    _, out = env
    a = post(1, LIST_A)
    verify.enqueue([hit(a)])

    def broken(ev, cfg):
        raise RuntimeError("claude rc=1")

    con = db.connect()
    for expected in (1, 2):
        verify.run(con, [{"identifier": IDENT, "note": "", "contact": ""}], ask=broken)
        assert [i["attempts"] for i in verify.load_queue()] == [expected]
    stats = verify.run(con, [{"identifier": IDENT, "note": "", "contact": ""}], ask=broken)
    con.close()
    assert stats["failed"] == 1 and verify.load_queue() == []
    assert "check by hand" in out["telegram"][-1]


def run_note(out, verdict, note):
    con = db.connect()
    try:
        return verify.run(con, [{"identifier": IDENT, "note": note, "contact": ""}],
                          ask=answer(verdict, 0.5, out=out))
    finally:
        con.close()


def test_copy_of_an_unclear_entry_is_reported_once(env):
    _, out = env
    a = post(1, LIST_A)
    verify.enqueue([hit(a)])
    run_note(out, "unclear", "")
    assert len(out["telegram"]) == 1
    copy = post(2, "Shared\n" + LIST_A)
    verify.enqueue([hit(copy)])
    stats = run_note(out, "unclear", "")
    assert stats["known"] == 1 and out["asked"] == [a] and len(out["telegram"]) == 1
    assert (IDENT, copy) not in watchlib.load_reviewed()          # still a visible match


def test_unclear_entry_is_read_again_when_the_registrant_adds_details(env):
    _, out = env
    a = post(1, LIST_A)
    verify.enqueue([hit(a)])
    run_note(out, "unclear", "")
    copy = post(2, "Shared\n" + LIST_A)
    verify.enqueue([hit(copy)])
    stats = run_note(out, "possible sighting", "اكسنت مضلع رمادي")
    assert out["asked"] == [a, copy] and stats["leads"] == 1
    assert out["cards"] == [copy]                     # now a lead, with a full card
    assert verify.load_leads()[0]["kind"] == "lead"


def test_an_unclear_entry_does_not_downgrade_a_later_sighting(env):
    _, out = env
    a = post(1, LIST_A)
    verify.enqueue([hit(a)])
    run_note(out, "unclear", "")
    b = post(2, LIST_B)
    verify.enqueue([hit(b)])
    run_note(out, "possible sighting", "")
    assert out["cards"] == [b]                        # first real lead: full card, not a line
    assert "NEW SOURCE" not in out["telegram"][-1]
