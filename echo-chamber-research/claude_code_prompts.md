# Claude Code prompts: census Days 1–2

Start each prompt in a **fresh** Claude Code session, launched from `~/geo-ai-scraper` with the venv active. Claude Code reads `census/CLAUDE.md` (the standing rules) by itself when it works in `census/`; each prompt also names it, to be safe.

---

## Prompt 1: fix the Tier A sampler (Day 1)

```
Read census/CLAUDE.md, then section 3 "Sampling" of echo-chamber-research/preregistration.md.
Change census/collect/tier_a.py so it follows section 3 exactly. Keep its COLLECT / SUMMARIZE split.
Don't call the live API in this session.

1. Settings -> new census/collect/census_config.py, imported by tier_a.py:
   CUTOFF = 2026-10-28T00:00:00Z (exclusive), WINDOW_DAYS = 180, N_VIDEOS = 15,
   SHORT_MAX_SECONDS = 180, SEED = 42, QUOTA_GUARD_UNITS = 1000.
   Remove WINDOW_START_DAYS_AGO and WINDOW_END_DAYS_AGO: the window is now fixed,
   [CUTOFF - 180 days, CUTOFF), the same on every run day. Record it in the run manifest.
2. SUMMARIZE: replace the random sample with a deterministic rule.
   Take the latest 15 eligible long-form videos, ordered by contentDetails.videoPublishedAt
   from the playlist items (not snippet.publishedAt).
   Eligible = in the window, duration > 180 s, no liveStreamingDetails,
   and snippet.liveBroadcastContent == "none".
   If fewer than 15 exist, take them all and record the shortfall.
   Compute comments_enabled_share, median_comment_count and median_view_count over the
   selected long-form videos only. Shorts and livestreams are counted over the span scanned.
3. COLLECT: page the uploads playlist newest-first. After each playlist page, call
   videos.list (part=snippet,statistics,contentDetails,liveStreamingDetails) only for that
   page's items inside the window. Stop when any of these holds:
   (a) at least 15 eligible long-form videos have been found AND one further page has been
       read (uploads order isn't strictly chronological);
   (b) a whole page is older than the window start;
   (c) there is no next page.
   This stops a news channel being paged back 180 days. Rework the quota guard estimate for
   this stopping rule.
4. Add --reuse-run RUN_TAG. For the handles given, rebuild the selection from that earlier
   run's raw files (the 11 Nov pilot run for @aajtak and @ndtvindia; its window ended exactly
   at CUTOFF). Then call videos.list for ONLY the 15 selected IDs (1 unit per channel) to
   refresh the stats, so every channel's stats come from the same run.
   Save these responses through Recorder in the current run.
5. Update SUMMARY_COLUMNS: handle, channel_id, subscribers, cutoff, window_start, n_selected,
   shortfall, shorts_scanned, livestreams_excluded, pages_fetched, comments_enabled_share,
   median_comment_count, median_comments_per_1000_views, median_view_count, units_used.
   In tier_a_videos.csv, add a selected flag and a reason for each excluded video.
6. Write census/tests/test_tier_a.py (pytest, offline, small fake responses) covering: a video
   after the cutoff, a Short, a livestream, an out-of-order old item on a later page, a channel
   with fewer than 15 eligible videos, comments disabled (commentCount missing -> None), and
   the stopping rule.
7. Finally, run the new SUMMARIZE in --reuse-run mode as a dry run, with no API calls, against
   the 11 Nov pilot raw files for @aajtak and @ndtvindia, and print only the counts:
   n_selected, shortfall, and the date range of the selected videos.
```

**Then check it yourself:** `pytest -q` and `git diff census/collect/tier_a.py`, then commit (the commands are in the reply).

---

## Prompt 2: candidate checks and blind coding sheets (Day 2)

Before this, fill in the blank handles in `echo-chamber-research/census_frame.csv`, add 1–2 candidates from logged-out searches, and add 2 cricket and 2 consumer-tech control candidates.

```
Read census/CLAUDE.md. Write census/collect/candidates.py, reusing Recorder and the helpers
from tier_a.py and the settings from census_config.py.
Input and output: echo-chamber-research/census_frame.csv.
You may call the live API in this session, up to 300 units. Never print video titles:
I am one of the blind coders.

For each row that has a handle:
1. channels.list forHandle (part=snippet,statistics,contentDetails) -> fill in channel_id,
   subscribers, last_upload_date and size_tier
   (large >= 1,000,000; mid 100,000–999,999; below that -> ineligible).
   hiddenSubscriberCount -> eligible_provisional = no.
2. Page the uploads playlist newest-first until you have the 10 most recent long-form videos
   (> 180 s, no liveStreamingDetails); at most 6 playlist pages per channel.
3. Political rows only: write census/coding/sheet_coder1.csv and census/coding/sheet_coder2.csv,
   identical, with columns item_id,title,code,note, rows shuffled with SEED across channels.
   Put the item_id -> handle, video_id mapping only in census/coding/key.csv.
   Control rows: no coding.

Write the filled columns back to census_frame.csv. Finish with a table of handle, subscribers,
size_tier, last_upload_date, n_titles_found, eligible_provisional (no titles), and the
units used.
```

---

## Prompt 3: agreement, channel labels and the draw (Day 2, after both sheets are coded)

```
Read census/CLAUDE.md, the "Stance coding" and "Candidates" paragraphs of
echo-chamber-research/preregistration.md, and echo-chamber-research/stance_codebook.md.
Write census/collect/score_stance.py. No API calls.

1. Validate both coded sheets in census/coding/: every code must be S, C or N.
   List any blank or invalid rows by item_id.
2. Report title-level Cohen's kappa and raw agreement.
3. Join the sheets to key.csv. For each coder separately, derive channel labels with the
   pre-registered rule: among S/C titles, >= 2/3 S -> supportive; >= 2/3 C -> critical;
   otherwise, or fewer than 3 S/C titles -> mixed.
   Write stance_coder1 and stance_coder2 into census_frame.csv, report channel-level
   agreement, and write disagreements to census/coding/disagreements.csv.
4. If any eligible political row is missing stance_final (I fill it in after arbitration),
   stop at this point and tell me which rows.
5. Otherwise, draw. For each format row (legacy, commentary), list every assignment of one
   eligible candidate per stance cell such that the row has >= 1 large and >= 1 mid
   channel. Pick one assignment with random.Random(SEED), enumerating in sorted handle order.
   Controls: pick one large and one mid the same way.
   Then put the remaining eligible candidates in each cell in a SEED-random order
   (draw_rank: 1 = first replacement). Leave empty cells empty.
   Write selected and draw_rank into census_frame.csv.

Print kappa, channel agreement, the 8-channel grid and any empty cells.
```

Run Prompt 3 twice: once after coding (it stops at step 4), and again after you've filled in `stance_final`.
