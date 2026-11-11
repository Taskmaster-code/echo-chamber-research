"""
Tier A feasibility census — public channel and video counts only. NO comments.

Run from ~/geo-ai-scraper with the venv active:
    python census/collect/tier_a.py @dhruvrathee @ShamSharmaShow
    python census/collect/tier_a.py @dhruvrathee --reuse-run 20261111T014602Z --reuse @aajtak @ndtvindia
    python census/collect/tier_a.py --reuse-run 20261111T014602Z --reuse @aajtak @ndtvindia --dry-run
    python census/collect/tier_a.py --summarize-only RUN_TAG    # re-process saved raw files

The script has two halves:
  COLLECT    talks to the API and saves every response untouched (no filtering).
  SUMMARIZE  reads ONLY the saved raw files, applies the sampling rule
             and writes the tables in census/data/processed/.
Because summarizing never calls the API, you can change the analysis rules later
and re-run it for free on the same raw data.

Sampling (preregistration section 3, settings in census_config.py):
  window    [CUTOFF - 180 days, CUTOFF), fixed, the same on every run day
  sample    the latest N_VIDEOS eligible long-form videos, newest first by the
            playlist item's contentDetails.videoPublishedAt
  eligible  in the window, duration > SHORT_MAX_SECONDS, no liveStreamingDetails,
            snippet.liveBroadcastContent == "none"
  paging    stop when (a) N_VIDEOS eligible found AND one more page read,
            (b) a whole page is older than the window start, or (c) no next page.
The same Scan class applies the stopping rule live (COLLECT) and on replay
(SUMMARIZE), so both always agree on which pages count.

Quota costs (default 10,000 units/day): channels.list, playlistItems.list and
videos.list each cost 1 unit per call. search.list is never used.
"""

import argparse
import csv
import gzip
import json
import math
import os
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from census_config import (CUTOFF, ESTIMATE_LONGFORM_SHARE, N_VIDEOS,
                           QUOTA_GUARD_UNITS, SHORT_MAX_SECONDS, WINDOW_DAYS,
                           WINDOW_START)

# ---------------------------------------------------------------- paths ------
# Paths are worked out from this file's location, so it works from any folder.
CENSUS_DIR = Path(__file__).resolve().parents[1]      # ~/geo-ai-scraper/census
PROJECT_DIR = CENSUS_DIR.parent                       # ~/geo-ai-scraper
RAW_DIR = CENSUS_DIR / "data" / "raw"
PROCESSED_DIR = CENSUS_DIR / "data" / "processed"
LEDGER_PATH = CENSUS_DIR / "quota_ledger.csv"

UNIT_COST = {"channels.list": 1, "playlistItems.list": 1, "videos.list": 1}
VIDEO_PARTS = "snippet,statistics,contentDetails,liveStreamingDetails"

SUMMARY_COLUMNS = [
    "handle", "channel_id", "subscribers", "cutoff", "window_start", "n_selected",
    "shortfall", "shorts_scanned", "livestreams_excluded", "pages_fetched",
    "comments_enabled_share", "median_comment_count",
    "median_comments_per_1000_views", "median_view_count", "units_used",
]

# Reasons a scanned playlist item is not in the sample ("" = selected).
REASONS = (
    "no_publish_date",            # private/deleted upload, no videoPublishedAt
    "after_cutoff",               # published on/after CUTOFF
    "before_window",              # published before WINDOW_START
    "unavailable",                # in window, but videos.list returned nothing
    "not_fetched_in_reused_run",  # in window, but the reused run never fetched it
    "livestream",                 # liveStreamingDetails present (past live/premiere)
    "live_or_upcoming",           # liveBroadcastContent is not "none"
    "duration_unknown",
    "short",                      # duration <= SHORT_MAX_SECONDS
    "not_in_latest_n",            # eligible, but older than the latest N_VIDEOS
)


# ----------------------------------------------------------------- helpers ---
def utc_now():
    return datetime.now(timezone.utc)


def parse_time(text):
    """'2026-10-20T03:30:05Z' -> timezone-aware datetime (None stays None)."""
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


ISO_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def duration_seconds(text):
    """ISO 8601 duration ('PT8H22M36S', 'P0D') -> seconds. None if unreadable."""
    match = ISO_DURATION.match(text or "")
    if not match:
        return None
    d, h, m, s = (int(x) if x else 0 for x in match.groups())
    return d * 86400 + h * 3600 + m * 60 + s


def to_int(value):
    """The API sends counts as strings ("12345"). Missing stays None, never 0."""
    return int(value) if value is not None else None


def days_between(later, earlier):
    return (later - earlier).total_seconds() / 86400


def item_id_and_date(item):
    """A playlistItems.list item -> (videoId, videoPublishedAt as datetime or None)."""
    cd = item.get("contentDetails", {})
    return cd.get("videoId"), parse_time(cd.get("videoPublishedAt"))


def in_window(published):
    return published is not None and WINDOW_START <= published < CUTOFF


def classify(published, detail, fetched_since=None):
    """Return 'eligible' or the reason this item is excluded.

    `detail` is the videos.list item (None if there is none). `fetched_since` is
    only set when replaying a reused run that fetched details from that date on.
    """
    if published is None:
        return "no_publish_date"
    if published >= CUTOFF:
        return "after_cutoff"
    if published < WINDOW_START:
        return "before_window"
    if detail is None:
        if fetched_since is not None and published < fetched_since:
            return "not_fetched_in_reused_run"
        return "unavailable"
    if "liveStreamingDetails" in detail:
        return "livestream"
    if detail.get("snippet", {}).get("liveBroadcastContent") != "none":
        return "live_or_upcoming"
    seconds = duration_seconds(detail.get("contentDetails", {}).get("duration"))
    if seconds is None:
        return "duration_unknown"
    if seconds <= SHORT_MAX_SECONDS:
        return "short"
    return "eligible"


# ------------------------------------------------------- the stopping rule ---
class Scan:
    """Walks uploads-playlist pages newest-first and applies the stopping rule.

    COLLECT feeds it pages as they arrive; SUMMARIZE replays saved pages through
    it. Either way it stops at the same page, so both agree on what was scanned.
    """

    def __init__(self, fetched_since=None):
        self.fetched_since = fetched_since
        self.order = []          # video IDs in the order first seen
        self.published = {}      # video_id -> datetime or None
        self.reason = {}         # video_id -> "eligible" or exclusion reason
        self.page_of = {}        # video_id -> page number (1 = newest)
        self.pages_read = 0
        self.n_eligible = 0
        self.reached_n_on_page = None
        self.stop_reason = None

    def new_window_ids(self, items):
        """IDs on this page that are in the window and not seen before (need videos.list)."""
        out = []
        for item in items:
            vid, published = item_id_and_date(item)
            if vid and vid not in self.reason and vid not in out and in_window(published):
                out.append(vid)
        return out

    def add_page(self, items, details, has_next):
        """Classify one page's items. Returns the stop reason, or None to keep paging."""
        if self.stop_reason:
            raise RuntimeError("scan already stopped")
        self.pages_read += 1
        dates = []
        for item in items:
            vid, published = item_id_and_date(item)
            if published is not None:
                dates.append(published)
            if not vid or vid in self.reason:          # duplicates: keep first sighting
                continue
            reason = classify(published, details.get(vid), self.fetched_since)
            self.order.append(vid)
            self.published[vid] = published
            self.reason[vid] = reason
            self.page_of[vid] = self.pages_read
            self.n_eligible += reason == "eligible"

        if self.reached_n_on_page is None and self.n_eligible >= N_VIDEOS:
            self.reached_n_on_page = self.pages_read
        if self.reached_n_on_page is not None and self.pages_read > self.reached_n_on_page:
            self.stop_reason = "found_n_plus_one_page"         # rule (a)
        elif dates and max(dates) < WINDOW_START:
            self.stop_reason = "page_before_window"            # rule (b)
        elif not has_next:
            self.stop_reason = "no_next_page"                  # rule (c)
        return self.stop_reason

    def selected(self):
        """The latest N_VIDEOS eligible IDs, newest first (ties broken by ID)."""
        eligible = [v for v in self.order if self.reason[v] == "eligible"]
        eligible.sort(key=lambda v: (-self.published[v].timestamp(), v))
        return eligible[:N_VIDEOS]

    def final_reasons(self):
        """video_id -> '' if selected, else the exclusion reason."""
        chosen = set(self.selected())
        return {v: "" if v in chosen else
                ("not_in_latest_n" if self.reason[v] == "eligible" else self.reason[v])
                for v in self.order}

    def count(self, reason):
        return sum(r == reason for r in self.reason.values())


# ------------------------------------------------------- raw data recorder ---
class Recorder:
    """Runs one API request at a time, saves the raw response, logs its quota.

    Every call goes through call(), so nothing can reach the API without being
    saved and counted. The API key lives only inside the client object; it is
    never part of `params`, so it never reaches a file or the screen.
    """

    def __init__(self, run_tag, run_date):
        self.run_tag = run_tag
        self.day_dir = RAW_DIR / run_date
        self.counter = 0
        self.units_by_channel = {}

    def call(self, endpoint, request, params, handle):
        self.counter += 1
        collected_at = utc_now().isoformat()
        error = None
        try:
            response = request.execute()
        except HttpError as e:
            # Keep Google's error body (it never contains the key). We do NOT use
            # str(e) or e.uri, because the request URL includes the key.
            try:
                response = json.loads(e.content)
            except ValueError:
                response = {"unparsed_error_body": e.content.decode("utf-8", "replace")}
            error = f"HTTP {e.status_code}: {e.reason}"

        units = UNIT_COST[endpoint]
        record = {
            "collected_at": collected_at,
            "endpoint": endpoint,
            "channel_handle": handle,
            "run_tag": self.run_tag,
            "params": params,
            "quota_cost": units,
            "response": response,      # the API's JSON, unchanged
        }
        out_dir = self.day_dir / endpoint
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{self.run_tag}_{self.counter:05d}_{handle.lstrip('@')}.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)   # keep Hindi text readable

        new_ledger = not LEDGER_PATH.exists()
        with open(LEDGER_PATH, "a", newline="") as f:
            writer = csv.writer(f)
            if new_ledger:
                writer.writerow(["time", "endpoint", "channel", "units"])
            writer.writerow([collected_at, endpoint, handle, units])
        self.units_by_channel[handle] = self.units_by_channel.get(handle, 0) + units

        if error:
            sys.exit(f"API error on {endpoint} for {handle}: {error} (saved to {path})")
        return response


# ----------------------------------------------------------------- collect ---
def estimate_remaining(rate_per_day, oldest_seen, n_eligible, longform_share):
    """Rough units still needed for one channel under the stopping rule.

    1) playlist pages from `oldest_seen` back to CUTOFF (no videos.list: nothing
       there is in the window);
    2) in-window pages until N_VIDEOS eligible are found, never more than the
       rest of the window holds, plus the one extra page of rule (a) / the stop
       page of rule (b). Each in-window page costs 2 units: the playlist page
       plus one videos.list call.
    """
    rate = max(rate_per_day, 0.01)
    pages_to_cutoff = math.ceil(rate * max(days_between(oldest_seen, CUTOFF), 0) / 50)
    need = max(N_VIDEOS - n_eligible, 0)
    window_days_left = max(days_between(min(oldest_seen, CUTOFF), WINDOW_START), 0)
    items = min(need / max(longform_share, 0.02), rate * window_days_left)
    in_window_pages = math.ceil(items / 50) + 1
    return pages_to_cutoff + 2 * in_window_pages


def confirm_over_budget(handle, estimate, assume_yes):
    print(f"  QUOTA GUARD: {handle} is estimated at ~{estimate} units (> {QUOTA_GUARD_UNITS}).")
    if assume_yes:
        print("  --yes given, continuing.")
        return True
    if sys.stdin.isatty():
        return input("  Continue with this channel? [y/N] ").strip().lower() == "y"
    print("  Not running interactively, so skipping this channel.")
    return False


def collect_channel(youtube, rec, handle, assume_yes):
    """Collect one channel. Returns 'ok', 'not_found' or 'skipped_quota'."""
    print(f"\n{handle}")

    # 1) Channel lookup: ID, subscriber count, uploads playlist.
    params = {"part": "snippet,statistics,contentDetails", "forHandle": handle}
    resp = rec.call("channels.list", youtube.channels().list(**params), params, handle)
    if not resp.get("items"):
        print("  channel not found")
        return "not_found"
    channel = resp["items"][0]
    uploads = channel["contentDetails"]["relatedPlaylists"]["uploads"]

    # 2) Quota guard, first pass (free): lifetime average upload rate.
    created = parse_time(channel["snippet"]["publishedAt"])
    total_videos = int(channel["statistics"].get("videoCount", 0))
    lifetime_rate = total_videos / max((utc_now() - created).days, 1)
    estimate = 1 + estimate_remaining(lifetime_rate, utc_now(), 0, ESTIMATE_LONGFORM_SHARE)
    print(f"  lifetime rate ~{lifetime_rate:.1f} videos/day -> estimate ~{estimate} units")
    guard_confirmed = False
    if estimate > QUOTA_GUARD_UNITS:
        if not confirm_over_budget(handle, estimate, assume_yes):
            return "skipped_quota"
        guard_confirmed = True

    # 3) Page the uploads playlist newest-first. After each page, fetch details
    #    for that page's in-window items only, then apply the stopping rule.
    scan = Scan()
    all_dates = []
    page_token = None
    while True:
        params = {"part": "contentDetails", "playlistId": uploads, "maxResults": 50}
        if page_token:
            params["pageToken"] = page_token
        resp = rec.call("playlistItems.list", youtube.playlistItems().list(**params),
                        params, handle)
        items = resp.get("items", [])
        page_token = resp.get("nextPageToken")

        details = {}
        ids = scan.new_window_ids(items)          # at most 50, so one call
        if ids:
            params = {"part": VIDEO_PARTS, "id": ",".join(ids)}
            vresp = rec.call("videos.list", youtube.videos().list(**params), params, handle)
            details = {v["id"]: v for v in vresp.get("items", [])}
        stop = scan.add_page(items, details, has_next=bool(page_token))
        if stop:
            break

        # Quota guard, running pass: re-estimate from everything seen so far.
        all_dates.extend(d for _, d in map(item_id_and_date, items) if d)
        if not guard_confirmed and len(all_dates) >= 2:
            span = max(days_between(max(all_dates), min(all_dates)), 1 / 24)
            rate = len(all_dates) / span
            judged = [r for r in scan.reason.values()
                      if r not in ("no_publish_date", "after_cutoff", "before_window",
                                   "unavailable")]
            share = (scan.n_eligible / len(judged) if len(judged) >= 20
                     else ESTIMATE_LONGFORM_SHARE)
            estimate = rec.units_by_channel[handle] + estimate_remaining(
                rate, min(all_dates), scan.n_eligible, share)
            if scan.pages_read == 1:
                print(f"  recent rate ~{rate:.1f} videos/day -> estimate ~{estimate} units")
            if estimate > QUOTA_GUARD_UNITS:
                if not confirm_over_budget(handle, estimate, assume_yes):
                    return "skipped_quota"
                guard_confirmed = True     # ask at most once per channel

    print(f"  {scan.pages_read} playlist pages, {scan.n_eligible} eligible long-form, "
          f"stop: {scan.stop_reason}")
    print(f"  units used: {rec.units_by_channel[handle]}")
    return "ok"


def collect_reused(youtube, rec, handle, reuse_tag):
    """Rebuild the selection from an earlier run, then refresh only those videos' stats."""
    print(f"\n{handle} (selection reused from run {reuse_tag})")
    scan = replay_reused(reuse_tag, handle)
    ids = scan.selected()
    if not ids:
        print("  nothing selected in the reused run")
        return "reuse_empty"
    params = {"part": VIDEO_PARTS, "id": ",".join(ids)}     # <= 15 IDs: 1 unit
    rec.call("videos.list", youtube.videos().list(**params), params, handle)
    print(f"  {len(ids)} selected videos refreshed, units used: {rec.units_by_channel[handle]}")
    return "ok"


# --------------------------------------------------------------- summarize ---
def load_manifest(run_tag):
    matches = list(RAW_DIR.glob(f"*/run_{run_tag}.json"))
    if not matches:
        sys.exit(f"No manifest found for run {run_tag}")
    return json.loads(matches[0].read_text())


def load_raw(run_tag):
    """Read every raw file from one run, grouped by channel and endpoint."""
    by_channel = {}
    for path in sorted(RAW_DIR.glob(f"*/*/{run_tag}_*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            record = json.load(f)
        channel = by_channel.setdefault(record["channel_handle"], {"units": 0})
        channel.setdefault(record["endpoint"], []).append(record)
        channel["units"] += record["quota_cost"]
    return by_channel


def details_by_id(data):
    return {item["id"]: item
            for rec in data.get("videos.list", [])
            for item in rec["response"].get("items", [])}


def replay(data, fetched_since=None):
    """Re-run the stopping rule over a channel's saved playlist pages (in saved order)."""
    details = details_by_id(data)
    pages = data.get("playlistItems.list", [])
    scan = Scan(fetched_since)
    for i, rec in enumerate(pages):
        has_next = bool(rec["response"].get("nextPageToken")) and i + 1 < len(pages)
        if scan.add_page(rec["response"].get("items", []), details, has_next):
            break
    return scan


def replay_reused(reuse_tag, handle):
    """Replay an earlier run for one handle. That run only fetched details for its
    own window, so items before its window start are marked as not fetched."""
    manifest = load_manifest(reuse_tag)
    if parse_time(manifest["window_end"]) != CUTOFF:
        sys.exit(f"Run {reuse_tag} window ended {manifest['window_end']}, not at CUTOFF; "
                 "its pages cannot be reused.")
    if manifest["status"].get(handle) != "ok":
        sys.exit(f"Run {reuse_tag} has no usable data for {handle}.")
    data = load_raw(reuse_tag).get(handle, {})
    return replay(data, fetched_since=parse_time(manifest["window_start"]))


def median_or_none(values):
    return statistics.median(values) if values else None


def channel_metrics(selected_ids, stats):
    """Metrics over the selected long-form videos only. `stats`: id -> videos.list item."""
    views, comments, per_1000 = [], [], []
    enabled = 0
    for vid in selected_ids:
        s = stats.get(vid, {}).get("statistics", {})
        v, c = to_int(s.get("viewCount")), to_int(s.get("commentCount"))  # None = disabled
        if v is not None:
            views.append(v)
        if c is not None:
            enabled += 1
            comments.append(c)
            if v:
                per_1000.append(1000 * c / v)
    n = len(selected_ids)
    return {
        "comments_enabled_share": round(enabled / n, 3) if n else None,
        "median_comment_count": median_or_none(comments),
        "median_comments_per_1000_views": (round(median_or_none(per_1000), 3)
                                           if per_1000 else None),
        "median_view_count": median_or_none(views),
    }


def summarize_channel(handle, scan, channel, stats, old_details, units):
    """One summary row plus one row per scanned video."""
    selected = scan.selected()
    reasons = scan.final_reasons()
    missing = [v for v in selected if v not in stats]
    if missing:
        print(f"{handle}: {len(missing)} selected videos missing from the stats call "
              "(private or deleted since)")

    row = {
        "handle": handle,
        "channel_id": channel["id"],
        "subscribers": to_int(channel["statistics"].get("subscriberCount")),  # absent if hidden
        "cutoff": CUTOFF.isoformat(),
        "window_start": WINDOW_START.isoformat(),
        "n_selected": len(selected),
        "shortfall": N_VIDEOS - len(selected),
        "shorts_scanned": scan.count("short"),
        "livestreams_excluded": scan.count("livestream"),
        "pages_fetched": scan.pages_read,
        **channel_metrics(selected, stats),
        "units_used": units,
    }

    video_rows = []
    for vid in scan.order:
        detail = stats.get(vid) if vid in selected else None
        detail = detail or old_details.get(vid) or {}
        s = detail.get("statistics", {})
        published = scan.published[vid]
        video_rows.append({
            "handle": handle,
            "video_id": vid,
            "title": detail.get("snippet", {}).get("title"),
            "video_published_at": published.isoformat() if published else None,
            "page": scan.page_of[vid],
            "duration_s": duration_seconds(detail.get("contentDetails", {}).get("duration")),
            "view_count": to_int(s.get("viewCount")),
            "comment_count": to_int(s.get("commentCount")),   # None = disabled
            "selected": reasons[vid] == "",
            "excluded_reason": reasons[vid],
        })
    return row, video_rows


def summarize(run_tag):
    manifest = load_manifest(run_tag)
    reused_from = manifest.get("reused_from", {})
    raw = load_raw(run_tag)

    rows, video_rows, samples = [], [], {}
    for handle in manifest["handles"]:
        status = manifest["status"].get(handle)
        if status != "ok":
            print(f"{handle}: not summarized ({status})")
            continue
        data = raw.get(handle, {"units": 0})

        if handle in reused_from:
            # Selection and channel info from the earlier run; stats from this run.
            old_tag = reused_from[handle]
            old = load_raw(old_tag).get(handle, {})
            scan = replay_reused(old_tag, handle)
            channel = old["channels.list"][0]["response"]["items"][0]
            stats, old_details = details_by_id(data), details_by_id(old)
        else:
            scan = replay(data)
            channel = data["channels.list"][0]["response"]["items"][0]
            stats = old_details = details_by_id(data)

        row, vrows = summarize_channel(handle, scan, channel, stats, old_details, data["units"])
        rows.append(row)
        video_rows.extend(vrows)
        samples[handle] = scan.selected()

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with open(PROCESSED_DIR / "tier_a_summary.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    if video_rows:
        with open(PROCESSED_DIR / "tier_a_videos.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(video_rows[0]))
            writer.writeheader()
            writer.writerows(video_rows)
    (PROCESSED_DIR / "tier_a_sample.json").write_text(json.dumps(
        {"run_tag": run_tag, "rule": f"latest {N_VIDEOS} eligible long-form by videoPublishedAt",
         "n_videos": N_VIDEOS, "cutoff": CUTOFF.isoformat(),
         "window_start": WINDOW_START.isoformat(), "reused_from": reused_from,
         "selected_video_ids": samples}, indent=2))
    print(f"\nWrote summary for {len(rows)} channels to {PROCESSED_DIR}")


def dry_run_reuse(reuse_tag, handles):
    """No API calls, no files written: show what --reuse-run would select."""
    for handle in handles:
        scan = replay_reused(reuse_tag, handle)
        sel = scan.selected()
        line = f"{handle}: n_selected={len(sel)} shortfall={N_VIDEOS - len(sel)}"
        if sel:
            dates = [scan.published[v] for v in sel]
            line += f" selected {min(dates):%Y-%m-%d %H:%M} to {max(dates):%Y-%m-%d %H:%M} UTC"
        print(line)
        print(f"  pages replayed={scan.pages_read} stop={scan.stop_reason}")
        gaps = scan.count("not_fetched_in_reused_run")
        if gaps:
            print(f"  WARNING: {gaps} in-window items have no details in run {reuse_tag}; "
                  "the selection may be incomplete. Collect this channel fresh instead.")


# -------------------------------------------------------------------- main ---
def main():
    parser = argparse.ArgumentParser(description="Tier A census (no comments).")
    parser.add_argument("handles", nargs="*", help="channels to collect fresh, e.g. @dhruvrathee")
    parser.add_argument("--yes", action="store_true", help="skip the quota-guard question")
    parser.add_argument("--summarize-only", metavar="RUN_TAG",
                        help="re-process an earlier run without calling the API")
    parser.add_argument("--reuse-run", metavar="RUN_TAG",
                        help="earlier run whose playlist pages are reused for --reuse handles")
    parser.add_argument("--reuse", nargs="+", default=[], metavar="HANDLE",
                        help="channels whose selection is rebuilt from --reuse-run")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --reuse-run: print the reused selection, call nothing")
    args = parser.parse_args()

    if args.summarize_only:
        summarize(args.summarize_only)
        return
    if bool(args.reuse_run) != bool(args.reuse):
        parser.error("--reuse-run and --reuse go together")
    if args.dry_run:
        if not args.reuse_run:
            parser.error("--dry-run needs --reuse-run and --reuse")
        dry_run_reuse(args.reuse_run, args.reuse)
        return
    overlap = set(args.handles) & set(args.reuse)
    if overlap:
        parser.error(f"listed both fresh and reused: {sorted(overlap)}")
    if not args.handles and not args.reuse:
        parser.error("give at least one channel handle")

    load_dotenv(PROJECT_DIR / ".env")
    api_key = os.getenv("YT_API_KEY")
    if not api_key:
        sys.exit("YT_API_KEY not found in .env")
    youtube = build("youtube", "v3", developerKey=api_key, cache_discovery=False)

    now = utc_now()
    run_tag = now.strftime("%Y%m%dT%H%M%SZ")
    run_date = now.strftime("%Y-%m-%d")
    print(f"Run {run_tag}: window {WINDOW_START:%Y-%m-%d} to {CUTOFF:%Y-%m-%d} "
          "(start included, cutoff excluded, UTC)")

    # The manifest records what this run was asked to do, so SUMMARIZE can
    # rebuild everything from disk later.
    manifest = {"run_tag": run_tag, "started_at": now.isoformat(),
                "cutoff": CUTOFF.isoformat(), "window_days": WINDOW_DAYS,
                "window_start": WINDOW_START.isoformat(), "window_end": CUTOFF.isoformat(),
                "n_videos": N_VIDEOS, "short_max_seconds": SHORT_MAX_SECONDS,
                "handles": args.reuse + args.handles,
                "reused_from": {h: args.reuse_run for h in args.reuse},
                "status": {}}
    manifest_path = RAW_DIR / run_date / f"run_{run_tag}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    rec = Recorder(run_tag, run_date)
    for handle in args.reuse:
        manifest["status"][handle] = collect_reused(youtube, rec, handle, args.reuse_run)
        manifest_path.write_text(json.dumps(manifest, indent=2))
    for handle in args.handles:
        manifest["status"][handle] = collect_channel(youtube, rec, handle, args.yes)
        manifest_path.write_text(json.dumps(manifest, indent=2))

    summarize(run_tag)


if __name__ == "__main__":
    main()
