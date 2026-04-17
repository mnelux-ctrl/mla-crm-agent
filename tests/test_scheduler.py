"""Unit tests for sending/scheduler.py."""

from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from sending.scheduler import (
    base_interval_for_count,
    compute_send_schedule,
    will_exceed_daily_limit,
    format_schedule_summary,
    JITTER_LOW,
    JITTER_HIGH,
    ABSOLUTE_MIN_GAP_SECONDS,
)


UTC = timezone.utc


# ── base_interval_for_count ─────────────────────────────────────────────────

@pytest.mark.parametrize("n,expected", [
    (1, 120), (3, 120), (5, 120),
    (6, 180), (15, 180), (20, 180),
    (21, 270), (42, 270), (50, 270),
    (51, 360), (100, 360), (500, 360),
])
def test_base_intervals(n, expected):
    assert base_interval_for_count(n) == expected


def test_zero_count_rejected():
    with pytest.raises(ValueError):
        base_interval_for_count(0)
    with pytest.raises(ValueError):
        base_interval_for_count(-1)


# ── compute_send_schedule: timing math ──────────────────────────────────────

def test_empty_schedule_for_zero():
    assert compute_send_schedule(0, datetime.now(UTC)) == []


def test_single_message_at_start():
    start = datetime(2026, 4, 17, 10, 0, 0, tzinfo=UTC)
    sched = compute_send_schedule(1, start)
    assert sched == [start]


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        compute_send_schedule(5, datetime(2026, 4, 17, 10, 0))


def test_first_message_always_at_start():
    start = datetime(2026, 4, 17, 10, 0, 0, tzinfo=UTC)
    for count in (2, 5, 42, 100):
        sched = compute_send_schedule(count, start, rng=random.Random(42))
        assert sched[0] == start


def test_gaps_monotonic_increasing():
    start = datetime(2026, 4, 17, 10, 0, 0, tzinfo=UTC)
    sched = compute_send_schedule(50, start, rng=random.Random(1))
    for i in range(1, len(sched)):
        assert sched[i] > sched[i - 1]


def test_gaps_respect_jitter_bounds():
    """All gaps should fall within [base*JITTER_LOW, base*JITTER_HIGH] (ignoring abs floor)."""
    start = datetime(2026, 4, 17, 10, 0, 0, tzinfo=UTC)
    count = 50
    base = base_interval_for_count(count)
    sched = compute_send_schedule(count, start, rng=random.Random(123))
    for i in range(len(sched) - 1):
        gap = (sched[i + 1] - sched[i]).total_seconds()
        assert gap >= max(ABSOLUTE_MIN_GAP_SECONDS, base * JITTER_LOW * 0.999)
        assert gap <= base * JITTER_HIGH * 1.001


def test_absolute_min_gap_enforced():
    """Even if tier tuning would produce < ABSOLUTE_MIN_GAP_SECONDS, floor holds."""
    start = datetime(2026, 4, 17, 10, 0, 0, tzinfo=UTC)
    # Force min_gap higher than base for count=5 (base=120) to prove the clamp
    sched = compute_send_schedule(5, start, rng=random.Random(7), min_gap_seconds=200)
    for i in range(len(sched) - 1):
        gap = (sched[i + 1] - sched[i]).total_seconds()
        assert gap >= 200


def test_stefan_5_emails_approximately_2min():
    """Stefan's explicit expectation: 5 emails → ~2 min gaps."""
    start = datetime(2026, 4, 17, 10, 0, 0, tzinfo=UTC)
    sched = compute_send_schedule(5, start, rng=random.Random(0))
    gaps = [(sched[i + 1] - sched[i]).total_seconds() for i in range(4)]
    # Base 120s ± 20% jitter → 96s to 144s
    for g in gaps:
        assert ABSOLUTE_MIN_GAP_SECONDS <= g <= 150


def test_stefan_50_emails_approximately_4_5min():
    """Stefan's explicit expectation: 50 emails → 4-5 min gaps."""
    start = datetime(2026, 4, 17, 10, 0, 0, tzinfo=UTC)
    sched = compute_send_schedule(50, start, rng=random.Random(0))
    gaps = [(sched[i + 1] - sched[i]).total_seconds() for i in range(49)]
    # Base 270s ± 20% → 216s to 324s → 3.6 to 5.4 min
    avg = sum(gaps) / len(gaps)
    assert 216 <= avg <= 324


def test_determinism_with_seeded_rng():
    start = datetime(2026, 4, 17, 10, 0, 0, tzinfo=UTC)
    a = compute_send_schedule(20, start, rng=random.Random(42))
    b = compute_send_schedule(20, start, rng=random.Random(42))
    assert a == b


# ── will_exceed_daily_limit ─────────────────────────────────────────────────

def test_daily_limit_not_exceeded():
    assert not will_exceed_daily_limit(100, 0, daily_limit=1500)
    assert not will_exceed_daily_limit(100, 1399, daily_limit=1500)


def test_daily_limit_exactly_hit():
    assert not will_exceed_daily_limit(100, 1400, daily_limit=1500)


def test_daily_limit_exceeded():
    assert will_exceed_daily_limit(101, 1400, daily_limit=1500)
    assert will_exceed_daily_limit(1, 1500, daily_limit=1500)


# ── format_schedule_summary ─────────────────────────────────────────────────

def test_summary_empty():
    s = format_schedule_summary([])
    assert s["count"] == 0
    assert s["first"] is None


def test_summary_single():
    t = datetime(2026, 4, 17, 10, 0, 0, tzinfo=UTC)
    s = format_schedule_summary([t])
    assert s["count"] == 1
    assert s["first"] == s["last"] == t.isoformat()
    assert s["gap_min_s"] == 0


def test_summary_multi():
    start = datetime(2026, 4, 17, 10, 0, 0, tzinfo=UTC)
    sched = compute_send_schedule(42, start, rng=random.Random(9))
    s = format_schedule_summary(sched)
    assert s["count"] == 42
    assert s["gap_min_s"] >= ABSOLUTE_MIN_GAP_SECONDS
    assert s["gap_max_s"] <= 360
