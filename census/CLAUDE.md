# Standing rules for Claude Code: echo-chamber census

## What this is
A small feasibility census of YouTube comment communities around Indian political channels. The design is fixed in `echo-chamber-research/preregistration.md`. Treat it as binding. If a task would change sampling, metrics or thresholds, stop and say so; don't change them silently. Stance coding rules are in `echo-chamber-research/stance_codebook.md`. The channel frame is `echo-chamber-research/census_frame.csv`.

## Layout (repo root = ~/geo-ai-scraper)
- `census/collect/`: collection scripts. `tier_a.py` keeps its COLLECT / SUMMARIZE split; reuse its `Recorder` and helpers rather than duplicating them.
- `census/data/raw/<date>/<endpoint>/`: raw API responses (gitignored).
- `census/data/processed/`: derived tables (gitignored).
- `census/coding/`: stance-coding sheets and `key.csv`.
- `census/quota_ledger.csv`: one row per API call.
- Analysis belongs in a notebook later, never in the collectors.

## Hard rules
1. **Never read, print or echo `.env`.** The key is `YT_API_KEY` and is loaded with python-dotenv. Never put it in params, logs or files.
2. **Never open or print files under `census/data/`.** Inspect them only through code that prints counts or summaries.
3. **No `search.list`.** Use only `channels.list`, `playlistItems.list`, `videos.list` and (Tier B) `commentThreads.list`.
4. **Quota:** every call goes through `Recorder` (saved and logged). Estimate the cost before a run. Ask before spending more than 300 units in one session, unless the prompt says otherwise. On `quotaExceeded`, stop; the quota resets at 13:30 IST.
5. **Don't call the live API** unless the prompt explicitly allows it. Tests must run offline with fixtures.
6. **Comments (Tier B):** hash author channel IDs at intake (SHA-256 with `HASH_SALT` from `.env`). Never store display names, profile images or comment text, except for the 100-comment language-validation sample.
7. **Blind coding:** never print video titles to the terminal while a stance-coding round is open.
8. Settings live in `census/collect/census_config.py`, not as constants scattered through scripts.
9. **Don't run `git commit`, `git push` or delete files.** Luo Ji does that.

## Environment
Ubuntu Linux, Python 3.14, venv at `~/geo-ai-scraper/venv`. Run tests with `pytest -q`.

## How to report back
Luo Ji is learning. End each session with:
- a short plain-language summary of what changed and why
- the test result
- the exact commands he should run to check it

Keep outputs short; never paste raw JSON.
