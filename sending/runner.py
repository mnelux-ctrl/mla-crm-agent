"""
sending/runner.py — APScheduler-powered runner for per-recipient campaign sends.

Each approved campaign schedules N `date`-trigger jobs (one per recipient).
Job payload is tiny — just campaign_id + person_id — and the job re-loads
campaign state at fire time, so pause/cancel is instant (we just un-schedule).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

import config
from airtable import client as airtable_client
from domain import campaign as campaign_domain
from sending.email_agent_client import send_now, SendNowError
from sending.scheduler import compute_send_schedule
from state import redis_client

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)
        _scheduler.start()
        logger.info("APScheduler started")
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


# ── Scheduling API ──────────────────────────────────────────────────────────

def job_id(campaign_id: str, person_id: str) -> str:
    return f"crm_send_{campaign_id}_{person_id}"


def schedule_campaign(campaign: campaign_domain.Campaign, start_at: datetime | None = None) -> int:
    """Schedule one APScheduler job per recipient. Returns number of jobs scheduled."""
    sched = get_scheduler()
    if start_at is None:
        start_at = datetime.now(timezone.utc)
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=timezone.utc)

    total = len(campaign.recipients)
    fire_times = compute_send_schedule(total, start_at)

    # Store scheduled_send_at back into recipient records
    for r, when in zip(campaign.recipients, fire_times):
        r["scheduled_send_at"] = when.isoformat()

    campaign.scheduled_start_at = start_at.isoformat()
    campaign_domain.save(campaign)

    scheduled_count = 0
    for r, when in zip(campaign.recipients, fire_times):
        jid = job_id(campaign.campaign_id, r["person_id"])
        sched.add_job(
            _send_one_recipient_sync,
            trigger=DateTrigger(run_date=when),
            id=jid,
            replace_existing=True,
            args=[campaign.campaign_id, r["person_id"], campaign.org_id],
            misfire_grace_time=3600,  # 1h grace if service restarts
        )
        redis_client.add_job_id(campaign.campaign_id, jid, org_id=campaign.org_id)
        scheduled_count += 1

    logger.info(
        f"Scheduled {scheduled_count} sends for campaign {campaign.campaign_id} "
        f"from {fire_times[0]} to {fire_times[-1]}"
    )
    return scheduled_count


def pause_campaign(campaign: campaign_domain.Campaign) -> int:
    """Remove all pending jobs for this campaign. Sent messages stay sent."""
    sched = get_scheduler()
    removed = 0
    for jid in redis_client.list_job_ids(campaign.campaign_id, org_id=campaign.org_id):
        try:
            sched.remove_job(jid)
            removed += 1
        except Exception:
            pass  # job already fired or missing
        redis_client.remove_job_id(campaign.campaign_id, jid, org_id=campaign.org_id)
    return removed


def resume_campaign(campaign: campaign_domain.Campaign) -> int:
    """Reschedule any recipients still in status='scheduled' from NOW forward."""
    pending = [r for r in campaign.recipients if r.get("status") == "scheduled"]
    if not pending:
        return 0

    sched = get_scheduler()
    now = datetime.now(timezone.utc)
    fire_times = compute_send_schedule(len(pending), now)

    scheduled = 0
    for r, when in zip(pending, fire_times):
        r["scheduled_send_at"] = when.isoformat()
        jid = job_id(campaign.campaign_id, r["person_id"])
        sched.add_job(
            _send_one_recipient_sync,
            trigger=DateTrigger(run_date=when),
            id=jid,
            replace_existing=True,
            args=[campaign.campaign_id, r["person_id"], campaign.org_id],
            misfire_grace_time=3600,
        )
        redis_client.add_job_id(campaign.campaign_id, jid, org_id=campaign.org_id)
        scheduled += 1

    campaign_domain.save(campaign)
    return scheduled


def cancel_campaign(campaign: campaign_domain.Campaign) -> int:
    n = pause_campaign(campaign)
    redis_client.clear_job_index(campaign.campaign_id, org_id=campaign.org_id)
    return n


# ── Per-recipient execution ─────────────────────────────────────────────────

def _send_one_recipient_sync(campaign_id: str, person_id: str, org_id: str) -> None:
    """Sync wrapper APScheduler can call. Delegates to async send_one."""
    try:
        asyncio.run(_send_one_recipient_async(campaign_id, person_id, org_id))
    except RuntimeError:
        # If there's already a running loop (shouldn't happen in APScheduler thread pool),
        # fall back to new loop in thread.
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_send_one_recipient_async(campaign_id, person_id, org_id))
        finally:
            loop.close()


async def _send_one_recipient_async(campaign_id: str, person_id: str, org_id: str) -> None:
    c = campaign_domain.load(campaign_id, org_id)
    if not c:
        logger.error(f"send: campaign {campaign_id} not found at fire time")
        return

    # If paused/cancelled between scheduling and firing, bail out gracefully.
    from airtable.schema import CampaignStatus
    if c.status not in (CampaignStatus.APPROVED, CampaignStatus.SENDING):
        logger.info(f"send: skipping campaign {campaign_id} (status={c.status})")
        return

    # Defense-in-depth: verify approval_token still exists in Redis before send.
    # A stale job (campaign revoked but scheduler fired anyway) would fail at
    # email-agent anyway, but we cut short to avoid the round-trip.
    if not c.approval_token or redis_client.lookup_token(c.approval_token, org_id=org_id) != c.campaign_id:
        logger.error(f"send: approval token for {campaign_id} missing/revoked — aborting")
        campaign_domain.record_send_failure(campaign_id, person_id, "approval_token_revoked", org_id=org_id)
        return

    recipient = next((r for r in c.recipients if r.get("person_id") == person_id), None)
    if not recipient:
        logger.error(f"send: recipient {person_id} not in campaign {campaign_id}")
        return

    # Auto-pause check: if this recipient already replied to a prior send in
    # the campaign, skip. Protects against multi-send embarrassment.
    if recipient.get("reply_received"):
        logger.info(f"send: recipient {person_id} already replied — auto-skipping")
        recipient["status"] = "skipped_replied"
        campaign_domain.save(c)
        return

    # Mark sending
    recipient["status"] = "sending"
    campaign_domain.save(c)

    # Native 1-to-1 B2B email — NO unsubscribe footer. This is a personal
    # send from Stefan, not a newsletter. If newsletters become a future use
    # case, opt-in the footer via campaign/template flag.
    try:
        result = await send_now(
            campaign_id=c.campaign_id,
            approval_token=c.approval_token,
            to=recipient["email"],
            subject=recipient["rendered_subject"],
            body=recipient["rendered_body"],
            cc=recipient.get("cc", []) or [],
            attachment_urls=c.attachments,
            recipient_person_id=recipient.get("person_id"),
        )

        gmail_message_id = result["gmail_message_id"]
        gmail_thread_id = result["gmail_thread_id"]

        # Airtable EMAIL_LOG write (email-agent already logs too, this is our ledger)
        try:
            airtable_client.log_outbound_email(
                campaign_id=c.campaign_id,
                recipient_person_id=recipient.get("person_id"),
                recipient_email=recipient["email"],
                subject=recipient["rendered_subject"],
                gmail_message_id=gmail_message_id,
                gmail_thread_id=gmail_thread_id,
                sent_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.warning(f"EMAIL_LOG write failed for {person_id} (non-fatal): {e}")

        try:
            source_table = recipient.get("source_table", "")
            if source_table:
                airtable_client.update_contact_last_outbound(
                    source_table,
                    recipient["person_id"],
                    gmail_thread_id,
                    datetime.now(timezone.utc),
                )
                # Also write last_sent_template for audit (campaign name ≈ template usage)
                try:
                    from airtable.schema import ContactField
                    api = airtable_client._get_api()
                    import config as _cfg
                    tbl = api.table(_cfg.AIRTABLE_BASE_ID, source_table)
                    tbl.update(
                        recipient["person_id"],
                        {ContactField.LAST_SENT_TEMPLATE: c.template_name or c.name},
                        typecast=True,
                    )
                except Exception:
                    pass
        except Exception:
            pass  # already soft-failed inside

        campaign_domain.record_send_success(
            c.campaign_id, person_id, gmail_message_id, gmail_thread_id, org_id=org_id
        )
        redis_client.increment_usage(1, org_id=org_id)
        redis_client.increment_day_send(1, org_id=org_id)  # atomic daily counter
        logger.info(f"Sent {person_id} for campaign {c.campaign_id} ({gmail_message_id})")

    except SendNowError as e:
        logger.error(f"Send failed for {person_id} in campaign {c.campaign_id}: {e}")
        try:
            airtable_client.log_send_error(
                campaign_id=c.campaign_id,
                recipient_person_id=recipient.get("person_id"),
                recipient_email=recipient["email"],
                subject=recipient["rendered_subject"],
                error=str(e),
            )
        except Exception:
            pass
        campaign_domain.record_send_failure(c.campaign_id, person_id, str(e), org_id=org_id)

    except Exception as e:
        logger.exception(f"Unexpected send error for {person_id}")
        campaign_domain.record_send_failure(c.campaign_id, person_id, f"unexpected: {e}", org_id=org_id)
    finally:
        redis_client.remove_job_id(c.campaign_id, job_id(c.campaign_id, person_id), org_id=org_id)
