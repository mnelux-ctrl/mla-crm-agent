"""Tests for scheduled campaign start (future scheduling)."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("CRM_API_KEY", "x")
os.environ.setdefault("CRM_INTERNAL_KEY", "x")
os.environ.setdefault("AIRTABLE_PAT", "x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("EMAIL_AGENT_URL", "http://x")
os.environ.setdefault("EMAIL_AGENT_INTERNAL_KEY", "x")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from api.campaigns import _resolve_scheduled_start
from sending.scheduler import now_utc


def test_approval_override_wins():
    future = (datetime.now(timezone.utc) + timedelta(days=25)).isoformat()
    campaign_default = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    result = _resolve_scheduled_start(future, campaign_default)
    assert abs((result - datetime.fromisoformat(future)).total_seconds()) < 1


def test_campaign_default_used_when_no_override():
    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    result = _resolve_scheduled_start("", future)
    assert abs((result - datetime.fromisoformat(future)).total_seconds()) < 1


def test_past_scheduled_time_rounds_to_now():
    """Safety: if Stefan fat-fingers a past date, don't silently lose the campaign."""
    past = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    result = _resolve_scheduled_start(past, "")
    # Must be close to now, not in the past
    delta = (result - now_utc()).total_seconds()
    assert abs(delta) < 2


def test_empty_falls_back_to_now():
    before = now_utc()
    result = _resolve_scheduled_start("", "")
    after = now_utc()
    assert before <= result <= after + timedelta(seconds=1)


def test_z_suffix_datetime_parsed():
    """UTC 'Z' suffix should parse correctly."""
    future_utc_z = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = _resolve_scheduled_start(future_utc_z, "")
    assert result > now_utc()


def test_invalid_datetime_falls_through():
    """Garbage input falls through to next candidate (or now)."""
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    result = _resolve_scheduled_start("not-a-date", future)
    # Should have used campaign_default
    assert abs((result - datetime.fromisoformat(future)).total_seconds()) < 1


def test_naive_iso_treated_as_utc():
    """ISO without timezone defaults to UTC — not a crash."""
    future_naive = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    result = _resolve_scheduled_start(future_naive, "")
    assert result.tzinfo is not None
    assert result > now_utc()


def test_25_days_ahead_preserved():
    """Stefan's explicit example: 'zakaži za 25 dana'."""
    future = datetime.now(timezone.utc) + timedelta(days=25, hours=2)
    iso = future.isoformat()
    result = _resolve_scheduled_start(iso, "")
    # Within 1 second of expected
    assert abs((result - future).total_seconds()) < 1


def test_tz_aware_positive_offset():
    """Europe/Podgorica in summer is +02:00."""
    future_pg = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0).astimezone(timezone(timedelta(hours=2))).isoformat()
    result = _resolve_scheduled_start(future_pg, "")
    assert result > now_utc()
