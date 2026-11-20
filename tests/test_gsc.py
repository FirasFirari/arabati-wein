"""Search Console monitor: what counts as a problem, and when an alert fires."""
import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import gsc_export as g


def snap(clicks_recent=3, clicks_before=3, impressions=20, pages=None, sitemaps=None):
    today = datetime.date.today()
    rows = []
    for i in range(g.FRESH_DAYS, g.FRESH_DAYS + 14):
        week_recent = i < g.FRESH_DAYS + 7
        rows.append({"keys": [(today - datetime.timedelta(days=i)).isoformat()],
                     "clicks": clicks_recent if week_recent else clicks_before,
                     "impressions": impressions if week_recent else 20})
    return {
        "end": today.isoformat(), "by_date": rows,
        "pages": pages if pages is not None else [
            {"url": "https://example.org/", "verdict": "PASS", "coverage": "Submitted and indexed"}],
        "sitemaps": sitemaps if sitemaps is not None else [
            {"errors": "0", "warnings": "0", "lastDownloaded": today.isoformat() + "T00:00:00Z"}],
    }


def test_healthy_site_has_no_problems():
    assert g.problems(snap()) == {}


def test_unindexed_page_is_a_problem():
    p = g.problems(snap(pages=[{"url": "https://example.org/a.html", "verdict": "NEUTRAL",
                                "coverage": "URL is unknown to Google"}]))
    assert list(p) == ["page:https://example.org/a.html"]


def test_click_drop_needs_volume():
    assert "clicks:drop" in g.problems(snap(clicks_recent=1, clicks_before=3))   # 7 vs 21
    assert "clicks:drop" not in g.problems(snap(clicks_recent=0, clicks_before=1))  # 0 vs 7: too few
    assert "clicks:drop" not in g.problems(snap(clicks_recent=2, clicks_before=3))  # 14 vs 21


def test_zero_impressions_and_incomplete_days_are_ignored():
    assert "impressions:zero" in g.problems(snap(impressions=0))
    # the newest FRESH_DAYS days have no rows at all in snap(): must not count as zero
    assert "impressions:zero" not in g.problems(snap())


def test_sitemap_errors_and_staleness():
    old = (datetime.date.today() - datetime.timedelta(days=40)).isoformat() + "T00:00:00Z"
    p = g.problems(snap(sitemaps=[{"errors": "2", "warnings": "0", "lastDownloaded": old}]))
    assert {"sitemap:errors", "sitemap:stale"} <= set(p)
    assert "sitemap:missing" in g.problems(snap(sitemaps=[]))


def test_alert_only_on_change(tmp_path):
    state = tmp_path / "state.json"
    assert g.compare({"a": "A"}, state) == ([], [])          # first run is a baseline
    assert g.compare({"a": "A"}, state) == ([], [])          # standing problem: silent
    assert g.compare({"a": "A", "b": "B"}, state) == (["B"], [])
    assert g.compare({"b": "B"}, state) == ([], ["A"])
