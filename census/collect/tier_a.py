"""
Tier A feasibility census — public channel and video counts only. NO comments.

Run from ~/geo-ai-scraper with the venv active:
    python census/collect/tier_a.py @aajtak @ndtvindia @dhruvrathee
    python census/collect/tier_a.py --summarize-only RUN_TAG    # re-process saved raw files

The script has two halves:
  COLLECT    talks to the API and saves every response untouched (no filtering).
  SUMMARIZE  reads ONLY the saved raw files, applies the window/labels/sampling
             and writes the tables in census/data/processed/.
Because summarizing never calls the API, you can change the analysis rules later
and re-run it for free on the same raw data.

Quota costs (default 10,000 units/day): channels.list, playlistItems.list and
videos.list each cost 1 unit per call. search.list is never used.
"""

import argparse
import csv
import gzip
import json
import math
import os
import random
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ---------------------------------------------------------------- settings ---
# Paths are worked out from this file's location, so it works from any folder.
CENSUS_DIR = Path(__file__).resolve().parents[1]      # ~/geo-ai-scraper/census
PROJECT_DIR = CENSUS_DIR.parent                       # ~/geo-ai-scraper
RAW_DIR = CENSUS_DIR / "data" / "raw"
PROCESSED_DIR = CENSUS_DIR / "data" / "processed"
LEDGER_PATH = CENSUS_DIR / "quota_ledger.csv"

WINDOW_START_DAYS_AGO = 28   # window = [today-28d 00:00 UTC, today-14d 00:00 UTC)
WINDOW_END_DAYS_AGO = 14
SAMPLE_SIZE = 15
SEED = 42
QUOTA_GUARD_UNITS = 1000     # ask before spending more than this on one channel
SHORT_MAX_SECONDS = 180      # Shorts can be up to 3 minutes
UNIT_COST = {"channels.list": 1, "playlistItems.list": 1, "videos.list": 1}

SUMMARY_COLUMNS = [
    "handle", "channel_id", "subscribers", "window_start", "window_end",
    "videos_in_window", "livestreams_excluded", "possible_shorts", "eligible",
    "sampled", "comments_enabled_share", "median_comment_count",
    "median_view_count", "min_comment_count", "max_comment_count", "units_used",
]


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


def compute_window(today):
    """Both edges are midnight UTC; start is included, end is excluded."""
    midnight = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    return (midnight - timedelta(days=WINDOW_START_DAYS_AGO),
            midnight - timedelta(days=WINDOW_END_DAYS_AGO))


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
def estimate_units(videos_per_day, newest, window_start, window_end):
    """Rough cost of one channel: pages back to window_start + videos.list calls."""
    days_back = max((newest - window_start).total_seconds() / 86400, 0)
    pages = math.ceil(videos_per_day * days_back / 50) + 1   # +1 for the stop page
    video_calls = math.ceil(videos_per_day * (window_end - window_start).days / 50)
    return 1 + pages + video_calls


def confirm_over_budget(handle, estimate, assume_yes):
    print(f"  QUOTA GUARD: {handle} is estimated at ~{estimate} units (> {QUOTA_GUARD_UNITS}).")
    if assume_yes:
        print("  --yes given, continuing.")
        return True
    if sys.stdin.isatty():
        return input("  Continue with this channel? [y/N] ").strip().lower() == "y"
    print("  Not running interactively, so skipping this channel.")
    return False


def collect_channel(youtube, rec, handle, window_start, window_end, assume_yes):
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
    estimate = estimate_units(lifetime_rate, utc_now(), window_start, window_end)
    print(f"  lifetime rate ~{lifetime_rate:.1f} videos/day -> estimate ~{estimate} units")
    if estimate > QUOTA_GUARD_UNITS and not confirm_over_budget(handle, estimate, assume_yes):
        return "skipped_quota"

    # 3) Page through the uploads playlist (newest first), 50 videos per page.
    #    Stop once a whole page is older than window_start.
    window_ids = []
    all_dates = []
    page_token, page_num = None, 0
    guard_confirmed = False
    while True:
        params = {"part": "contentDetails", "playlistId": uploads, "maxResults": 50}
        if page_token:
            params["pageToken"] = page_token
        resp = rec.call("playlistItems.list", youtube.playlistItems().list(**params),
                        params, handle)
        page_num += 1

        dates = []
        for item in resp.get("items", []):
            published = parse_time(item["contentDetails"].get("videoPublishedAt"))
            if published is None:      # private/deleted videos have no date
                continue
            dates.append(published)
            if window_start <= published < window_end:
                window_ids.append(item["contentDetails"]["videoId"])

        # Quota guard, running pass: after EVERY page, re-estimate from all dates
        # seen so far. One page of a news channel covers only a few hours, and
        # night-time hours upload far less, so a single page under-estimates.
        # units already spent + projected remaining playlist pages + videos.list calls.
        all_dates.extend(dates)
        if not guard_confirmed and len(all_dates) >= 2:
            span_days = max((max(all_dates) - min(all_dates)).total_seconds() / 86400, 1 / 24)
            rate = len(all_dates) / span_days
            days_left = max((min(all_dates) - window_start).total_seconds() / 86400, 0)
            pages_left = math.ceil(rate * days_left / 50) + 1
            video_calls = math.ceil(rate * (window_end - window_start).days / 50)
            estimate = rec.units_by_channel[handle] + pages_left + video_calls
            if page_num == 1:
                print(f"  recent rate ~{rate:.1f} videos/day -> estimate ~{estimate} units")
            if estimate > QUOTA_GUARD_UNITS:
                if not confirm_over_budget(handle, estimate, assume_yes):
                    return "skipped_quota"
                guard_confirmed = True     # ask at most once per channel

        page_token = resp.get("nextPageToken")
        if not page_token:
            break                                   # reached the oldest upload
        if dates and max(dates) < window_start:
            break                                   # whole page is before the window
    print(f"  {page_num} playlist pages, {len(window_ids)} videos in window")

    # 4) Video details for every in-window video, 50 IDs per call.
    window_ids = list(dict.fromkeys(window_ids))    # drop duplicates, keep order
    for start in range(0, len(window_ids), 50):
        params = {"part": "snippet,statistics,contentDetails,liveStreamingDetails",
                  "id": ",".join(window_ids[start:start + 50])}
        rec.call("videos.list", youtube.videos().list(**params), params, handle)
    print(f"  units used: {rec.units_by_channel[handle]}")
    return "ok"


# --------------------------------------------------------------- summarize ---
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


def label_video(item):
    """Apply the three labels to one videos.list item."""
    stats = item.get("statistics", {})
    seconds = duration_seconds(item["contentDetails"].get("duration"))
    return {
        "video_id": item["id"],
        "title": item["snippet"]["title"],
        "published_at": item["snippet"]["publishedAt"],
        "duration_s": seconds,
        "view_count": to_int(stats.get("viewCount")),
        "comment_count": to_int(stats.get("commentCount")),   # None = disabled
        "past_livestream": "liveStreamingDetails" in item,
        "possible_short": seconds is not None and seconds <= SHORT_MAX_SECONDS,
    }


def summarize(run_tag):
    manifest_path = next(RAW_DIR.glob(f"*/run_{run_tag}.json"))
    manifest = json.loads(manifest_path.read_text())
    window_start = parse_time(manifest["window_start"])
    window_end = parse_time(manifest["window_end"])
    raw = load_raw(run_tag)

    rows, video_rows, samples = [], [], {}
    for handle in manifest["handles"]:
        data = raw.get(handle, {})
        status = manifest["status"].get(handle)
        if status != "ok":
            print(f"{handle}: not summarized ({status})")
            continue

        channel = data["channels.list"][0]["response"]["items"][0]

        # Window membership is re-derived from the saved playlist pages.
        in_window = {}
        for rec in data.get("playlistItems.list", []):
            for item in rec["response"].get("items", []):
                published = parse_time(item["contentDetails"].get("videoPublishedAt"))
                if published and window_start <= published < window_end:
                    in_window[item["contentDetails"]["videoId"]] = True

        videos = [label_video(item)
                  for rec in data.get("videos.list", [])
                  for item in rec["response"].get("items", [])]
        missing = len(in_window) - len(videos)
        if missing:
            print(f"{handle}: {missing} in-window videos not returned by videos.list "
                  "(private or deleted since upload)")

        eligible = [v for v in videos if not v["past_livestream"]]
        enabled = [v for v in eligible if v["comment_count"] is not None]
        comments = [v["comment_count"] for v in enabled]
        views = [v["view_count"] for v in eligible if v["view_count"] is not None]

        # Random sample: sort IDs first so the result depends only on the seed,
        # then reseed per channel so each channel's sample is reproducible alone.
        random.seed(SEED)
        eligible_ids = sorted(v["video_id"] for v in eligible)
        sampled = random.sample(eligible_ids, min(SAMPLE_SIZE, len(eligible_ids)))
        samples[handle] = sampled

        for v in videos:
            video_rows.append({"handle": handle, **v, "sampled": v["video_id"] in sampled})

        subs = channel["statistics"].get("subscriberCount")   # absent if hidden
        rows.append({
            "handle": handle,
            "channel_id": channel["id"],
            "subscribers": to_int(subs),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "videos_in_window": len(in_window),
            "livestreams_excluded": sum(v["past_livestream"] for v in videos),
            "possible_shorts": sum(v["possible_short"] for v in eligible),
            "eligible": len(eligible),
            "sampled": len(sampled),
            "comments_enabled_share": round(len(enabled) / len(eligible), 3) if eligible else None,
            "median_comment_count": statistics.median(comments) if comments else None,
            "median_view_count": statistics.median(views) if views else None,
            "min_comment_count": min(comments) if comments else None,
            "max_comment_count": max(comments) if comments else None,
            "units_used": data["units"],
        })

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
        {"run_tag": run_tag, "seed": SEED, "sample_size": SAMPLE_SIZE,
         "window_start": manifest["window_start"], "window_end": manifest["window_end"],
         "sampled_video_ids": samples}, indent=2))
    print(f"\nWrote summary for {len(rows)} channels to {PROCESSED_DIR}")


# -------------------------------------------------------------------- main ---
def main():
    parser = argparse.ArgumentParser(description="Tier A census (no comments).")
    parser.add_argument("handles", nargs="*", help="channel handles, e.g. @aajtak")
    parser.add_argument("--yes", action="store_true", help="skip the >1000-unit question")
    parser.add_argument("--summarize-only", metavar="RUN_TAG",
                        help="re-process an earlier run without calling the API")
    args = parser.parse_args()

    if args.summarize_only:
        summarize(args.summarize_only)
        return
    if not args.handles:
        parser.error("give at least one channel handle")

    load_dotenv(PROJECT_DIR / ".env")
    api_key = os.getenv("YT_API_KEY")
    if not api_key:
        sys.exit("YT_API_KEY not found in .env")
    youtube = build("youtube", "v3", developerKey=api_key, cache_discovery=False)

    now = utc_now()
    run_tag = now.strftime("%Y%m%dT%H%M%SZ")
    run_date = now.strftime("%Y-%m-%d")
    window_start, window_end = compute_window(now.date())
    print(f"Run {run_tag}: window {window_start:%Y-%m-%d} to {window_end:%Y-%m-%d} "
          "(start included, end excluded, UTC)")

    # The manifest records what this run was asked to do, so SUMMARIZE can
    # rebuild everything from disk later.
    manifest = {"run_tag": run_tag, "started_at": now.isoformat(),
                "window_start": window_start.isoformat(), "window_end": window_end.isoformat(),
                "handles": args.handles, "status": {}}
    manifest_path = RAW_DIR / run_date / f"run_{run_tag}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    rec = Recorder(run_tag, run_date)
    for handle in args.handles:
        manifest["status"][handle] = collect_channel(
            youtube, rec, handle, window_start, window_end, args.yes)
        manifest_path.write_text(json.dumps(manifest, indent=2))

    summarize(run_tag)


if __name__ == "__main__":
    main()
