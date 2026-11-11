"""Offline tests for census/collect/tier_a.py. No network: the API is faked."""

import csv
import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collect"))
import tier_a  # noqa: E402
from census_config import CUTOFF, N_VIDEOS, WINDOW_START  # noqa: E402


# ------------------------------------------------------------ fake data ------
def ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def pl_item(vid, published):
    return {"contentDetails": {"videoId": vid, "videoPublishedAt": ts(published)}}


def video(vid, seconds=600, views="1000", comments="10", live=False, lbc="none"):
    stats = {"viewCount": views}
    if comments is not None:
        stats["commentCount"] = comments
    item = {"id": vid, "snippet": {"title": f"t{vid}", "liveBroadcastContent": lbc},
            "contentDetails": {"duration": f"PT{seconds}S"}, "statistics": stats}
    if live:
        item["liveStreamingDetails"] = {"actualStartTime": "x"}
    return item


def hours_before_cutoff(h):
    return CUTOFF - timedelta(hours=h)


class FakeRequest:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


class FakeYouTube:
    """Serves playlist pages in order and videos.list from a lookup table; logs calls."""

    def __init__(self, pages, videos):
        self.pages, self.table, self.calls = pages, videos, []

    def channels(self):
        return self

    def playlistItems(self):
        return self

    def videos(self):
        return self

    def list(self, **params):
        if "forHandle" in params:
            self.calls.append(("channels", None))
            return FakeRequest({"items": [{
                "id": "UC1", "snippet": {"publishedAt": "2010-01-01T00:00:00Z"},
                "statistics": {"videoCount": "500", "subscriberCount": "1000"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]})
        if "playlistId" in params:
            i = int(params.get("pageToken", "0"))
            self.calls.append(("playlist", i))
            resp = {"items": self.pages[i]}
            if i + 1 < len(self.pages):
                resp["nextPageToken"] = str(i + 1)
            return FakeRequest(resp)
        ids = params["id"].split(",")
        self.calls.append(("videos", ids))
        return FakeRequest({"items": [self.table[v] for v in ids if v in self.table]})


@pytest.fixture
def tmp_census(tmp_path, monkeypatch):
    monkeypatch.setattr(tier_a, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(tier_a, "PROCESSED_DIR", tmp_path / "processed")
    monkeypatch.setattr(tier_a, "LEDGER_PATH", tmp_path / "ledger.csv")
    return tmp_path


def run_scan(pages, videos):
    scan = tier_a.Scan()
    for i, items in enumerate(pages):
        if scan.add_page(items, videos, has_next=i + 1 < len(pages)):
            break
    return scan


# ---------------------------------------------------------- classification ---
def test_video_after_cutoff_is_excluded():
    assert tier_a.classify(CUTOFF, video("a")) == "after_cutoff"
    assert tier_a.classify(CUTOFF - timedelta(seconds=1), video("a")) == "eligible"
    assert tier_a.classify(WINDOW_START - timedelta(seconds=1), video("a")) == "before_window"
    assert tier_a.classify(WINDOW_START, video("a")) == "eligible"


def test_short_is_excluded_at_180s_and_longform_starts_at_181s():
    t = hours_before_cutoff(1)
    assert tier_a.classify(t, video("a", seconds=180)) == "short"
    assert tier_a.classify(t, video("a", seconds=181)) == "eligible"


def test_livestreams_and_upcoming_are_excluded():
    t = hours_before_cutoff(1)
    assert tier_a.classify(t, video("a", seconds=7200, live=True)) == "livestream"
    assert tier_a.classify(t, video("a", lbc="upcoming")) == "live_or_upcoming"
    assert tier_a.classify(t, None) == "unavailable"


# ------------------------------------------------------------- the scan -------
def test_out_of_order_items_on_later_page():
    # Page 1: 15 eligible, 1..15 hours before cutoff. Rule (a) needs one more page.
    page1 = [pl_item(f"p1_{i}", hours_before_cutoff(i)) for i in range(1, 16)]
    # Page 2: one item NEWER than most of page 1, plus one far older than the window.
    late = pl_item("late", hours_before_cutoff(1.5))
    ancient = pl_item("ancient", WINDOW_START - timedelta(days=30))
    page2 = [late, ancient]
    page3 = [pl_item("never_read", hours_before_cutoff(100))]
    videos = {f"p1_{i}": video(f"p1_{i}") for i in range(1, 16)}
    videos["late"] = video("late")

    scan = run_scan([page1, page2, page3], videos)
    assert scan.stop_reason == "found_n_plus_one_page"
    assert scan.pages_read == 2                          # page 3 never read
    selected = scan.selected()
    assert "late" in selected and "p1_15" not in selected   # newer item displaces oldest
    reasons = scan.final_reasons()
    assert reasons["ancient"] == "before_window"
    assert reasons["p1_15"] == "not_in_latest_n"
    assert selected == sorted(selected, key=lambda v: scan.published[v], reverse=True)


def test_fewer_than_15_eligible_records_shortfall():
    page1 = ([pl_item(f"e{i}", hours_before_cutoff(i)) for i in range(1, 6)]
             + [pl_item("s1", hours_before_cutoff(7)), pl_item("l1", hours_before_cutoff(8))])
    page2 = [pl_item("old", WINDOW_START - timedelta(days=1))]   # whole page too old
    page3 = [pl_item("older", WINDOW_START - timedelta(days=2))]
    videos = {f"e{i}": video(f"e{i}") for i in range(1, 6)}
    videos["s1"] = video("s1", seconds=45)
    videos["l1"] = video("l1", live=True)

    scan = run_scan([page1, page2, page3], videos)
    assert scan.stop_reason == "page_before_window"
    assert len(scan.selected()) == 5
    assert scan.count("short") == 1 and scan.count("livestream") == 1


def test_stop_when_no_next_page():
    scan = run_scan([[pl_item("a", hours_before_cutoff(1))]], {"a": video("a")})
    assert scan.stop_reason == "no_next_page" and scan.selected() == ["a"]


def test_comments_disabled_counts_as_none_not_zero():
    stats = {"a": video("a", views="2000", comments="20"),
             "b": video("b", views="1000", comments=None)}
    m = tier_a.channel_metrics(["a", "b"], stats)
    assert m["comments_enabled_share"] == 0.5
    assert m["median_comment_count"] == 20              # b is not counted as 0
    assert m["median_comments_per_1000_views"] == 10.0
    assert m["median_view_count"] == 1500


# ------------------------------------------- collect + summarize (faked API) --
def test_collect_follows_stopping_rule_and_summary(tmp_census, monkeypatch):
    after = [pl_item(f"new{i}", CUTOFF + timedelta(hours=i)) for i in range(3)]
    page2 = [pl_item(f"v{i}", hours_before_cutoff(i)) for i in range(1, 17)]
    page2 += [pl_item("short", hours_before_cutoff(20)), pl_item("live", hours_before_cutoff(21))]
    page3 = [pl_item("x", hours_before_cutoff(30))]
    page4 = [pl_item("never", hours_before_cutoff(40))]
    videos = {f"v{i}": video(f"v{i}") for i in range(1, 17)}
    videos["v3"] = video("v3", comments=None)          # comments disabled
    videos.update(short=video("short", seconds=30), live=video("live", live=True),
                  x=video("x"))
    yt = FakeYouTube([after, page2, page3, page4], videos)

    rec = tier_a.Recorder("TAG", "2026-11-11")
    assert tier_a.collect_channel(yt, rec, "@chan", assume_yes=False) == "ok"

    # 1 channels + 3 playlist pages; videos.list only for pages with in-window items.
    kinds = [k for k, _ in yt.calls]
    assert kinds == ["channels", "playlist", "playlist", "videos", "playlist", "videos"]
    assert all(not v.startswith("new") for k, ids in yt.calls if k == "videos" for v in ids)
    assert rec.units_by_channel["@chan"] == 6

    manifest = {"run_tag": "TAG", "handles": ["@chan"], "status": {"@chan": "ok"},
                "window_start": WINDOW_START.isoformat(), "window_end": CUTOFF.isoformat()}
    (tmp_census / "raw" / "2026-11-11" / "run_TAG.json").write_text(json.dumps(manifest))
    tier_a.summarize("TAG")

    with open(tmp_census / "processed" / "tier_a_summary.csv") as f:
        row = next(csv.DictReader(f))
    assert list(row) == tier_a.SUMMARY_COLUMNS
    assert row["n_selected"] == "15" and row["shortfall"] == "0"
    assert row["pages_fetched"] == "3"
    assert row["shorts_scanned"] == "1" and row["livestreams_excluded"] == "1"
    assert row["comments_enabled_share"] == "0.933"
    assert row["units_used"] == "6"
    with open(tmp_census / "processed" / "tier_a_videos.csv") as f:
        vrows = {r["video_id"]: r for r in csv.DictReader(f)}
    assert vrows["new0"]["excluded_reason"] == "after_cutoff"
    assert vrows["v16"]["excluded_reason"] == "not_in_latest_n"
    assert vrows["v1"]["selected"] == "True" and vrows["v1"]["excluded_reason"] == ""


def test_reuse_replays_old_run_and_flags_unfetched(tmp_census):
    # Old run: window [CUTOFF-14d, CUTOFF); 3 eligible inside it, then older items
    # that are in the new 180-day window but were never fetched.
    old_start = CUTOFF - timedelta(days=14)
    page1 = [pl_item(f"v{i}", hours_before_cutoff(i)) for i in range(1, 4)]
    page2 = [pl_item("gap", old_start - timedelta(days=1))]
    rec = tier_a.Recorder("OLD", "2026-11-11")
    yt = FakeYouTube([page1, page2], {})
    rec.call("channels.list", yt.list(forHandle="@c"), {}, "@c")
    for i, page in enumerate([page1, page2]):
        resp = {"items": page, **({"nextPageToken": "1"} if i == 0 else {})}
        rec.call("playlistItems.list", FakeRequest(resp), {}, "@c")
    rec.call("videos.list", FakeRequest({"items": [video(f"v{i}") for i in range(1, 4)]}),
             {}, "@c")
    manifest = {"run_tag": "OLD", "handles": ["@c"], "status": {"@c": "ok"},
                "window_start": old_start.isoformat(), "window_end": CUTOFF.isoformat()}
    (tmp_census / "raw" / "2026-11-11" / "run_OLD.json").write_text(json.dumps(manifest))

    scan = tier_a.replay_reused("OLD", "@c")
    assert scan.selected() == ["v1", "v2", "v3"]
    assert scan.count("not_fetched_in_reused_run") == 1

    # The refresh call asks for the selected IDs only (one call, one unit).
    new_rec = tier_a.Recorder("NEW", "2026-11-11")
    yt2 = FakeYouTube([], {f"v{i}": video(f"v{i}", views="5000") for i in range(1, 4)})
    assert tier_a.collect_reused(yt2, new_rec, "@c", "OLD") == "ok"
    assert yt2.calls == [("videos", ["v1", "v2", "v3"])]
    assert new_rec.units_by_channel["@c"] == 1
