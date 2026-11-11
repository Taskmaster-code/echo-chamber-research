# Pre-registration: YouTube comment-community feasibility census

**Author:** Luo Ji (BITS Pilani, Hyderabad) · **Supervisor:** [professor's name] · **Draft v0.1:** 11 Nov 2026 · **To be committed to Git:** Day 2, before any Tier B (comment) data is collected. Later changes go in an *Amendments* log with date and reason.

Items marked **[DECIDE]** are still open. Thresholds in §5 are **proposals for the supervisor to confirm**.

## 1. Purpose

To check whether the comment communities around each *kind* of Indian political YouTube channel yield enough data to study closure, evidence response and reasons, before building a full collector. The census is a feasibility check, not a measure of Indian opinion.

## 2. Channel selection

**Eligibility (all must hold).**

- Main output is Indian political news or commentary (controls: main output is non-political).
- Primary language English, Hindi (Devanagari) or Hinglish, judged from titles and descriptions; recorded per channel.
- Public subscriber count of at least 100,000 (see size rule).
- At least 15 eligible long-form videos in the sampling window (§3), and at least one upload in the 30 days before the cutoff.
- Comments not disabled channel-wide; not an official party channel; not a made-for-kids channel.

**Grid: 8 channels.** Six political channels, one per cell of format × stance, plus two non-political controls.

| Format \ Stance toward central government | Supportive | Critical | Mixed |
|---|---|---|---|
| Legacy (TV / established news brand) | 1 | 1 | 1 |
| Commentary (independent creator) | 1 | 1 | 1 |
| Controls (non-political, same languages) | 2 channels **[DECIDE: categories, e.g. cricket, consumer tech]**; no exam-prep channels, whose commenters are largely minors | | |

**Size rule [DECIDE thresholds].** Proposed: each format row contains at least one *large* channel (≥1M subscribers) and at least one *mid-size* channel (100k–999k); the two controls are one large and one mid-size.

**Candidates.** From channel lists published in earlier Indian YouTube studies, neutral searches in a logged-out private window, and a starter pool suggested by Claude; each candidate's source and inclusion reason are recorded in `census_frame.csv`. Where a cell has more than one eligible candidate that also satisfies the size rule, the channel is drawn at random (seed 42). If a selected channel fails eligibility at Tier A, the next drawn candidate in its cell replaces it. An empty cell is reported as empty, not filled from another cell.

**Stance coding.** For each candidate, two coders independently label the 10 most recent long-form titles at the time of coding (Day 2), presented in shuffled order across channels: S (supportive of the central government), C (critical) or N (no stance / not about it). Proposed channel rule **[DECIDE]**: among titles coded S or C, ≥2/3 S → supportive; ≥2/3 C → critical; otherwise, or if fewer than 3 titles carry a stance → mixed. Each coder derives the channel label separately. Report title-level Cohen's κ and channel-level agreement. Disagreements are settled by **[DECIDE: supervisor arbitration / discussion]**. Coding uses titles only, before any comment data is seen. Stance labels are used only to fill the grid, never as an outcome. Second coder: **[DECIDE]**.

## 3. Sampling

- **Cutoff C**, the same for every channel: 28 Oct 2026 00:00 UTC (05:30 IST), exclusive. This is the end of the pilot window, so the Aaj Tak / NDTV pilot pages can be reused, and every video is ≥ 14 days old on any collection day from 11 Nov. **[DECIDE: confirm]**
- **Window:** published in [C − 180 days, C), i.e. from 1 May 2026 00:00 UTC. Paging stops once 15 are found.
- **Video sample:** the latest 15 eligible **long-form** videos (duration > 180 s) in the window, from `playlistItems.list` + `videos.list`. No `search.list`.
- **Exclusions:** past livestreams and premieres (`liveStreamingDetails` present). **Shorts** (≤ 180 s) are flagged and counted in Tier A, but excluded from Tier B and the primary metrics.
- **Seed 42** for every random step: channel draws within cells and the language-validation sample.
- **Comments (Tier B):** `commentThreads.list`, top-level threads only, `order=time`, `textFormat=plainText`, `maxResults=100`; cap 2,000 threads per video; every cap hit recorded. `commentsDisabled` (403) and removed videos (404) are recorded, not retried.
- **Event-window check** (20 Apr–15 May 2026): Tier A only, for commentary channels only. Legacy: recorded as *not checked*.

## 4. Metrics (six only)

Per channel; "sampled videos" means the 15 long-form videos. Comments are retrieved top-level comments, excluding those posted by the channel's own account.

| # | Metric | Exact definition |
|---|---|---|
| M1 | Long-form median comments | Median of `commentCount` (from `videos.list` at the Tier A run) over sampled videos with comments enabled; also the median of 1,000 × `commentCount` / `viewCount` over videos with views > 0. Note: `commentCount` includes replies. |
| M2 | Comments-enabled share | Sampled videos with comments enabled ÷ sampled videos. Enabled means `commentCount` is present and `commentThreads` does not return `commentsDisabled`. |
| M3 | Unique commenters | Number of distinct hashed author IDs among retrieved comments on the channel's sampled videos. |
| M4 | Repeat-commenter share | Commenters with comments on ≥ 2 distinct sampled videos of the channel ÷ M3. A lower bound, because of the cap. |
| M5 | Overlap coefficient | For channels A, B: \|A ∩ B\| ÷ min(\|A\|, \|B\|) over their hashed-ID sets; computed for all 28 pairs and shown as a heatmap ordered by format, then stance, then controls. Summary: median over the 15 political–political pairs vs median over the 12 political–control pairs. |
| M6 | Language mix | Share of retrieved comments in four classes: **Devanagari / Latin-English / Latin-Hindi-or-mixed / Other**. Before classifying, strip URLs, @mentions, hashtags, digits, emoji and punctuation. No letters left → Other. ≥ 50% of letters Devanagari → Devanagari. ≥ 50% Latin → Latin-Hindi-or-mixed if the text contains ≥ 2 tokens from a fixed romanised-Hindi function-word list (list frozen in Git before Tier B; words that are also common English words are excluded), otherwise Latin-English. Anything else → Other. **[DECIDE: the ≥ 2-token rule and the word list]** |

**Language validation.** 100 comments, drawn at random (seed 42) and spread across channels, are hand-labelled in the same four classes by two coders who have not seen the classifier output. Report agreement between the coders and the classifier's accuracy against their consensus. If accuracy is below 85% **[DECIDE]**, switch to IndicLID and re-validate.

## 5. Decision thresholds: **proposals for the supervisor to confirm**

**Usable channel:** M2 ≥ 80%, M1 ≥ 200, M3 ≥ 1,000.

| Criterion | Proposed threshold |
|---|---|
| C1 Stance coverage | At least one usable political channel in each stance column (supportive, critical, mixed) |
| C2 Format coverage | At least one usable channel in each format row |
| C3 Regulars | Median M4 over usable political channels ≥ 10% |
| C4 Informative overlap | ≥ 5 of the 15 political pairs have 0.01 ≤ M5 ≤ 0.50, **and** the political–political median exceeds the political–control median |
| C5 Language ID | Classifier accuracy ≥ 85% on the validation set (after the IndicLID upgrade, if needed) |

- **Go:** all five criteria are met. Build the full collector and apply to the Researcher Program with the census results.
- **Adjust:** C1 holds and at most two of C2–C5 fail. Narrow the scope openly. A C2 failure on legacy channels means restricting the study to commentary channels; a C5 failure means the language upgrade; a C4 failure means the closure question relies on within-channel measures.
- **Pivot:** C1 fails, fewer than 3 political channels are usable, or C3 and C4 both fail. Rethink the platform or the design.

Language mix (M6) has no threshold, because the scope now covers three languages. **[DECIDE: whether to add a minimum Hindi or Hinglish share]**

## 6. Pilot disclosure

Before this pre-registration, a Tier A pilot (video-level statistics only; no comments collected) was run on 4 channels for 14–28 Oct 2026, costing 1,072 quota units. The author has seen these results:

| Channel | Videos in window | Possible Shorts | Median comments | Median views |
|---|---|---|---|---|
| @aajtak | 8,676 | 6,256 | 0 (long-form 3) | 7,504 |
| @ndtvindia | 9,336 | 7,070 | 0 (long-form 7) | 9,572 |
| @dhruvrathee | 2 | 0 | 20,489 | 15.1M |
| @ShamSharmaShow | 27 | 22 | 169 | 201,750 |

The usable-channel bar (80% / 200 / 1,000), the 10% regulars bar and the overlap band were first written in `census_procedure.md` before the pilot **[CONFIRM pilot run date]**. Criteria C2 and C5, the Adjust rule for a legacy-only failure, and the 85% language bar were set *after* seeing the pilot. The pilot already suggests that legacy channels will fail M1, so a C2 failure is expected and is stated here in advance. Aaj Tak and NDTV raw pages are reused. Whether @dhruvrathee and @ShamSharmaShow may be drawn into the commentary cells: **[DECIDE]**. Any pilot channel that is selected is flagged in all outputs.

## 7. Exploratory questions (no thresholds; reported descriptively)

1. Commentary vs legacy engagement by stance: M1 per 1,000 views across the format × stance grid.
2. Shorts vs long-form: Shorts share of uploads and their `commentCount`, by format.
3. Event-window feasibility (commentary channels only): the number of 20 Apr–15 May 2026 videos still available, and their comment counts.
4. Language mix by format and stance, and whether Latin-Hindi-or-mixed is large enough to need the full five-class scheme.
5. Whether commenter overlap groups more by stance than by format (heatmap reading only).
6. Cap-hit rate by channel, as a sign of where the 2,000-thread cap distorts M3–M5.

## 8. Data handling and ethics

- Public data only, through the standard YouTube Data API v3. The API key is restricted and kept in `.env`; `.env` and `data/` are in `.gitignore`.
- **Hashing at intake:** author channel IDs are hashed with SHA-256 and a secret salt from `.env`, using one salt for every channel so that overlap stays measurable. Display names and profile images are never stored.
- **Comments:** raw Tier B responses are not stored. At intake each comment becomes one row of a minimal pseudonymised table (hashed ID, video ID, channel, timestamp, language label; no text, no names), kept for at most 30 days so metrics can be recomputed. After that the table and the salt are deleted. The 100 validation comments are the only stored text; they are kept separately and deleted once coded.
- **Outputs** are channel-level aggregates only: `census_table.csv`, the heatmap and the decision memo. No comment text, usernames or individual-level results appear anywhere.
- **Policy:** metrics use only API data combined by simple arithmetic (counts, medians, ratios), with no composite channel "scores". Results stay internal to the course and the supervisor; nothing from the census is published.
- **Known limitations:** audience geography is unavailable, so diaspora commenters are part of the measured communities. Shorts are detected by duration only. Time-ordered capping favours newer comments on large videos.
- **Ethics clearance:** **[DECIDE with the supervisor]** whether institutional review is needed before Tier B, since this is person-level data, even if pseudonymised.

## Open decisions (summary)

Second coder, arbiter · size thresholds · cutoff C · controls · stance rule · word list, ≥2-token rule, 85% bar · two commentary pilot channels · §5 thresholds · Hindi-share criterion · ethics clearance · pilot run date.

## Amendments

*(none yet)*
