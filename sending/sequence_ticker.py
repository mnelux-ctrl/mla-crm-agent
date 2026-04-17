"""
sending/sequence_ticker.py — APScheduler cron job that advances sequences.

Runs every 5 minutes (configurable). Queries Airtable for contacts whose
sequence_next_at is due, sends their next step, updates state.

Safe to run concurrently with one-shot campaign sends — each uses its own
rate limiting (day counter + tier gaps).
"""

from __future__ import annotations

import asyncio
import logging
import os

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from sending.runner import get_scheduler

logger = logging.getLogger(__name__)

TICK_INTERVAL_MINUTES = int(os.getenv("SEQ_TICK_INTERVAL_MIN", "5"))
# Don't fire sequence sends outside business hours (UTC-configurable)
# Europe/Podgorica: 8am–19pm is 07:00–18:00 UTC in summer, 07:00–18:00 in winter
BUSINESS_HOURS_START = int(os.getenv("SEQ_BUSINESS_HOUR_START", "8"))
BUSINESS_HOURS_END = int(os.getenv("SEQ_BUSINESS_HOUR_END", "19"))


def _tick_sync() -> None:
    """Sync wrapper APScheduler can call. Delegates to async tick."""
    from datetime import datetime
    now = datetime.now()
    # Business hours gate: don't send a follow-up at 3am local
    if not (BUSINESS_HOURS_START <= now.hour < BUSINESS_HOURS_END):
        logger.debug(f"seq-tick: outside business hours {BUSINESS_HOURS_START}-{BUSINESS_HOURS_END}; skipping")
        return
    try:
        from domain.sequence import tick_due_steps
        asyncio.run(tick_due_steps())
    except RuntimeError:
        # nested loop — run in fresh one
        loop = asyncio.new_event_loop()
        try:
            from domain.sequence import tick_due_steps
            loop.run_until_complete(tick_due_steps())
        finally:
            loop.close()
    except Exception:
        logger.exception("seq-tick crashed (non-fatal — next tick will retry)")


def start_sequence_ticker() -> None:
    """Register the sequence tick job with APScheduler. Idempotent."""
    sched = get_scheduler()
    job_id = "mla-crm-sequence-ticker"
    # Remove any prior job with the same ID so restarts replace cleanly
    try:
        sched.remove_job(job_id)
    except Exception:
        pass
    sched.add_job(
        _tick_sync,
        trigger=IntervalTrigger(minutes=TICK_INTERVAL_MINUTES),
        id=job_id,
        replace_existing=True,
        next_run_time=None,  # wait for first interval instead of running at startup
        misfire_grace_time=TICK_INTERVAL_MINUTES * 60 * 2,
    )
    logger.info(f"Sequence ticker scheduled every {TICK_INTERVAL_MINUTES} min "
                f"(business hours {BUSINESS_HOURS_START}:00-{BUSINESS_HOURS_END}:00 Europe/Podgorica)")
