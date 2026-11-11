"""
Census settings, in one place (CLAUDE.md rule 8).

The sampling values come from section 3 of echo-chamber-research/preregistration.md.
That document is binding: change a value here only if the preregistration changes.
"""

from datetime import datetime, timedelta, timezone

# Cutoff C: 28 Oct 2026 00:00 UTC (05:30 IST), EXCLUSIVE. Same for every channel.
CUTOFF = datetime(2026, 10, 28, 0, 0, 0, tzinfo=timezone.utc)

# Window = [CUTOFF - WINDOW_DAYS, CUTOFF). Fixed, so it is the same on every run day.
WINDOW_DAYS = 180
WINDOW_START = CUTOFF - timedelta(days=WINDOW_DAYS)     # 1 May 2026 00:00 UTC

N_VIDEOS = 15              # latest N eligible long-form videos per channel
SHORT_MAX_SECONDS = 180    # duration <= this is a Short; long-form is > this
SEED = 42                  # for every random step (none left in Tier A)

QUOTA_GUARD_UNITS = 1000   # ask before spending more than this on one channel

# Used ONLY by the quota estimate (never by sampling): the share of in-window
# uploads assumed to be eligible long-form before any have been seen.
ESTIMATE_LONGFORM_SHARE = 0.25
