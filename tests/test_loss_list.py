"""A broker's list of owners' loss reports is not a list of found cars."""
from carhunt.gates import LOSS_NOTE, check_entries, is_loss_list

HEAD = "Next Time\nposted to\nالبلاغات اليوميه\nالبلاغات الجديده\n"
LOSS = HEAD + ("اذا لقيت اي عربيه تعال الخاص وأثبت لي انها عندك .. وانا أسلمك رقم المالك فورا\n"
               "77385 / خ 5 توسان 2016\n076102 / توسان 2016\n")


def test_every_wording_seen_in_the_corpus():
    for line in (
            "اذا لقيت اي عربيه تعال الخاص وأثبت لي انها عندك .. وانا أسلمك رقم المالك فورا",
            "انته عندك اي عربيه وهي موجوده في الكشف تعال الخاص  وأنا أسلمك المالك مباشره",
            "انته لقيت عربيه هنا .. تعال الخاص أسلمك رقم المالك مباشره",
            "عندك اي عربيه هنا تعال خاص أرسلك رقم المالك فورا"):
        assert is_loss_list(HEAD + line + "\n020480 بوكس دبل أسود"), line


def test_found_lists_and_sale_ads_are_not_loss_lists():
    # the same publisher's FOUND list: come to me and I point you to the post
    assert not is_loss_list("( كشف جديد  12 )\nاذا تتطابق مع بيانات عربيتك ..   تعال خاص   "
                            "لأدلك بمكان المنشور وتاريخ المنشور\n41019 / خ #")
    assert not is_loss_list("سونتا 2001 مكان المعاينة الخرطوم مافي عمولة خش خاص واديك رقم المالك مباشر")
    assert not is_loss_list("حصر عربات موجودة في الخرطوم\nصالون 466588")


def test_gate_relabels_whatever_the_lane_said():
    entries = [{"plate": "77385 / خ 5", "status": "seeking_owner", "description": "توسان"},
               {"chassis": "076102", "status": "found", "description": None}]
    changes = check_entries(entries, LOSS, photos_unread=False)
    assert [e["status"] for e in entries] == ["unknown", "unknown"]
    assert entries[0]["description"] == f"{LOSS_NOTE}: توسان"
    assert entries[1]["description"] == LOSS_NOTE
    assert entries[0]["plate"] and entries[1]["chassis"]          # identifiers untouched
    assert any("loss-report list" in c for c in changes)
    # a second pass (re-ingest of a repaired row) changes nothing
    assert check_entries(entries, LOSS, photos_unread=False) == []
    assert entries[0]["description"] == f"{LOSS_NOTE}: توسان"


def test_ordinary_list_keeps_its_status():
    entries = [{"chassis": "466588", "status": "found", "description": "صالون"}]
    check_entries(entries, "حصر عربات موجودة في الخرطوم\nصالون 466588", photos_unread=False)
    assert entries[0]["status"] == "found" and entries[0]["description"] == "صالون"
