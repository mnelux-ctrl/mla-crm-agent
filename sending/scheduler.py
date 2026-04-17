"""
sending/scheduler.py — Pure timing math for rate-limited outreach.

Stefan's rule (verbatim): "5 emails → 2min razmaka; 50 emails → 4-5min razmaka.
Ako ih ima 50, onda mora bar 4-5min razmaka između poslatih emailova."

Intervals are non-linear — bigger batches need proportionally safer gaps to avoid
Gmail spam flagging. Jitter ± 20% so the send pattern doesn't look robotic.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone


# Batch size tiers (upper bound inclusive → base interval seconds)
# Tuned from Stefan's explicit intent: 5→120, 50→270, bigger→360.
TIER_THRESHOLDS: list[tuple[int, int]] = [
    (5,   120),   # ≤ 5   → 2 min
    (20,  180),   # ≤ 20  → 3 min
    (50,  270),   # ≤ 50  → 4.5 min
    (10**9, 360), # > 50  → 6 min
]

# ± jitter bounds on each interval (1.0 = no jitter).
JITTER_LOW = 0.8
JITTER_HIGH = 1.2

# Gmail daily cap (headroom under 2000 Workspace cap). Configurable via env.
DEFAULT_GMAIL_DAILY_LIMIT = 1500

# Absolute floor on any single gap, regardless of tier/jitter. Protects against
# future tier tuning that accidentally goes below a safe floor.
ABSOLUTE_MIN_GAP_SECONDS = 90


def base_interval_for_count(count: int) -> int:
    """Return the base interval (seconds) before jitter, for a batch of `count`."""
    if count <= 0:
        raise ValueError(f"count must be positive, got {count}")
    for upper, seconds in TIER_THRESHOLDS:
        if count <= upper:
            return seconds
    return TIER_THRESHOLDS[-1][1]  # unreachable, but defensive


def compute_send_schedule(
    count: int,
    start_at: datetime,
    *,
    rng: random.Random | None = None,
    min_gap_seconds: int = ABSOLUTE_MIN_GAP_SECONDS,
) -> list[datetime]:
    """Return fire-times for `count` sends starting at `start_at`.

    First message fires at `start_at`. Each subsequent message offset by
    `base_interval * uniform(JITTER_LOW, JITTER_HIGH)`, clamped to
    `min_gap_seconds` floor.

    rng param lets tests inject a seeded Random for determinism.
    """
    if count <= 0:
        return []
    if start_at.tzinfo is None:
        # Naive datetimes are a footgun with APScheduler; require TZ awareness.
        raise ValueError("start_at must be timezone-aware")

    rng = rng or random.Random()
    base = base_interval_for_count(count)

    schedule: list[datetime] = []
    t = start_at
    for i in range(count):
        schedule.append(t)
        if i < count - 1:
            raw_gap = base * rng.uniform(JITTER_LOW, JITTER_HIGH)
            gap = max(float(min_gap_seconds), raw_gap)
            t = t + timedelta(seconds=gap)
    return schedule


def will_exceed_daily_limit(
    planned_count: int,
    already_sent_today: int,
    *,
    daily_limit: int = DEFAULT_GMAIL_DAILY_LIMIT,
) -> bool:
    """True if sending `planned_count` more today would cross Gmail's daily cap."""
    return (already_sent_today + planned_count) > daily_limit


def format_schedule_summary(schedule: list[datetime]) -> dict:
    """Small dict for Slack preview: count, first/last fire time, gap range."""
    if not schedule:
        return {"count": 0, "first": None, "last": None, "gap_min_s": 0, "gap_max_s": 0}
    if len(schedule) == 1:
        return {
            "count": 1,
            "first": schedule[0].isoformat(),
            "last": schedule[0].isoformat(),
            "gap_min_s": 0,
            "gap_max_s": 0,
        }
    gaps = [
        (schedule[i + 1] - schedule[i]).total_seconds()
        for i in range(len(schedule) - 1)
    ]
    return {
        "count": len(schedule),
        "first": schedule[0].isoformat(),
        "last": schedule[-1].isoformat(),
        "gap_min_s": int(min(gaps)),
        "gap_max_s": int(max(gaps)),
    }


def now_utc() -> datetime:
    """Small helper; imports kept minimal to stay pure-ish."""
    return datetime.now(timezone.utc)
