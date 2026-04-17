"""
domain/campaign.py — Campaign lifecycle + Redis-backed hot state + Airtable audit.

Campaign state machine:

  drafting → awaiting_approval → approved → sending → completed
                   ↓                ↓          ↓         ↓
                cancelled       cancelled  cancelled  paused → resumed/cancelled

Hot state in Redis (crm:{org}:campaign:{id}), durable audit in CRM_CAMPAIGN Airtable.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import config
from airtable import client as airtable_client
from airtable.schema import CampaignField, CampaignStatus
from state import redis_client

logger = logging.getLogger(__name__)


@dataclass
class RecipientDraft:
    """Per-recipient state held inside campaign.recipients."""
    person_id: str
    email: str
    person_name: str
    rendered_subject: str
    rendered_body: str
    scheduled_send_at: str   # ISO
    status: str = "scheduled"   # scheduled / sending / sent / failed / skipped
    gmail_message_id: str = ""
    gmail_thread_id: str = ""
    error: str = ""


@dataclass
class Campaign:
    campaign_id: str
    name: str
    template_name: str
    segment_filter_json: dict
    master_subject: str
    master_body: str
    attachments: list[str] = field(default_factory=list)
    recipients: list[dict] = field(default_factory=list)      # list of RecipientDraft as dict
    status: str = CampaignStatus.DRAFTING
    approval_token: str = ""
    approved_slack_ts: str = ""
    scheduled_start_at: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str = ""
    sent_count: int = 0
    failed_count: int = 0
    reply_count: int = 0
    airtable_record_id: str = ""
    org_id: str = "mla"

    # ── Serialization ──────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "name": self.name,
            "template_name": self.template_name,
            "segment_filter_json": self.segment_filter_json,
            "master_subject": self.master_subject,
            "master_body": self.master_body,
            "attachments": self.attachments,
            "recipients": self.recipients,
            "status": self.status,
            "approval_token": self.approval_token,
            "approved_slack_ts": self.approved_slack_ts,
            "scheduled_start_at": self.scheduled_start_at,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "sent_count": self.sent_count,
            "failed_count": self.failed_count,
            "reply_count": self.reply_count,
            "airtable_record_id": self.airtable_record_id,
            "org_id": self.org_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Campaign":
        return cls(**d)

    # ── State transitions ──────────────────────────────────────────────

    def transition_to(self, new_status: str) -> None:
        if not CampaignStatus.can_transition(self.status, new_status):
            raise ValueError(
                f"Invalid transition {self.status} → {new_status} for campaign {self.campaign_id}"
            )
        self.status = new_status
        if new_status in (CampaignStatus.COMPLETED, CampaignStatus.CANCELLED):
            self.completed_at = datetime.now(timezone.utc).isoformat()


# ── Factory + persistence ───────────────────────────────────────────────────

def new_campaign_id() -> str:
    return str(uuid.uuid4())


def new_approval_token() -> str:
    return secrets.token_urlsafe(32)


def save(campaign: Campaign) -> None:
    """Persist campaign to Redis (hot) + Airtable (durable)."""
    redis_client.save_campaign_state(campaign.campaign_id, campaign.to_dict(), org_id=campaign.org_id)
    try:
        fields = _airtable_fields(campaign)
        if campaign.airtable_record_id:
            airtable_client.update_campaign(campaign.airtable_record_id, fields)
        else:
            rec = airtable_client.create_campaign(fields)
            campaign.airtable_record_id = rec["id"]
            # Re-save with the Airtable ID now that we have it
            redis_client.save_campaign_state(campaign.campaign_id, campaign.to_dict(), org_id=campaign.org_id)
    except Exception as e:
        logger.warning(f"Airtable sync for campaign {campaign.campaign_id} failed (non-fatal): {e}")


def load(campaign_id: str, org_id: str | None = None) -> Campaign | None:
    state = redis_client.load_campaign_state(campaign_id, org_id)
    if not state:
        return None
    return Campaign.from_dict(state)


def _airtable_fields(c: Campaign) -> dict[str, Any]:
    """Map Campaign dataclass → Airtable field dict (only persistable fields)."""
    return {
        CampaignField.CAMPAIGN_ID: c.campaign_id,
        CampaignField.NAME: c.name,
        CampaignField.STATUS: c.status,
        CampaignField.RECIPIENT_COUNT: len(c.recipients),
        CampaignField.SENT_COUNT: c.sent_count,
        CampaignField.FAILED_COUNT: c.failed_count,
        CampaignField.REPLY_COUNT: c.reply_count,
        CampaignField.SCHEDULED_START_AT: c.scheduled_start_at,
        CampaignField.APPROVAL_TOKEN: c.approval_token,
        CampaignField.APPROVED_SLACK_TS: c.approved_slack_ts,
        CampaignField.CREATED_AT: c.created_at,
        CampaignField.COMPLETED_AT: c.completed_at,
        CampaignField.MASTER_DRAFT_SUBJECT: c.master_subject,
        CampaignField.MASTER_DRAFT_BODY: c.master_body,
        CampaignField.ORG_ID: c.org_id,
    }


# ── Approval token lifecycle ───────────────────────────────────────────────

def approve(campaign_id: str, slack_ts: str, org_id: str | None = None) -> Campaign:
    """Idempotent approve: if already approved/sending, returns existing campaign
    without minting a new token or re-scheduling. Protects against double-clicks."""
    c = load(campaign_id, org_id)
    if not c:
        raise LookupError(f"Campaign {campaign_id} not found")
    if c.status in (CampaignStatus.APPROVED, CampaignStatus.SENDING):
        logger.info(f"Campaign {campaign_id} already in status={c.status}; idempotent approve noop")
        return c
    c.transition_to(CampaignStatus.APPROVED)
    c.approval_token = new_approval_token()
    c.approved_slack_ts = slack_ts
    save(c)
    redis_client.save_approval_token(c.approval_token, c.campaign_id, org_id=c.org_id)
    return c


def verify_token(campaign_id: str, token: str, org_id: str | None = None) -> bool:
    """Called by email-agent before each send. Must be cheap + safe."""
    if not campaign_id or not token:
        return False
    looked = redis_client.lookup_token(token, org_id)
    if looked != campaign_id:
        return False
    c = load(campaign_id, org_id)
    if not c:
        return False
    if c.status not in (CampaignStatus.APPROVED, CampaignStatus.SENDING):
        return False
    if c.approval_token != token:
        return False
    return True


def revoke_token(campaign_id: str, org_id: str | None = None) -> None:
    c = load(campaign_id, org_id)
    if not c:
        return
    if c.approval_token:
        redis_client.revoke_token(c.approval_token, org_id=c.org_id)
        c.approval_token = ""
        save(c)


# ── Progress updates ───────────────────────────────────────────────────────

def record_send_success(
    campaign_id: str,
    person_id: str,
    gmail_message_id: str,
    gmail_thread_id: str,
    org_id: str | None = None,
) -> None:
    c = load(campaign_id, org_id)
    if not c:
        return
    for r in c.recipients:
        if r.get("person_id") == person_id:
            r["status"] = "sent"
            r["gmail_message_id"] = gmail_message_id
            r["gmail_thread_id"] = gmail_thread_id
            break
    c.sent_count += 1
    if c.status == CampaignStatus.APPROVED:
        c.transition_to(CampaignStatus.SENDING)
    # Completion check
    total = len(c.recipients)
    if c.sent_count + c.failed_count >= total and c.status == CampaignStatus.SENDING:
        c.transition_to(CampaignStatus.COMPLETED)
        redis_client.revoke_token(c.approval_token, org_id=c.org_id)
    save(c)
    redis_client.publish_event(c.campaign_id, {
        "type": "message.sent",
        "campaign_id": c.campaign_id,
        "person_id": person_id,
        "sent_count": c.sent_count,
        "total": total,
    }, org_id=c.org_id)


def record_send_failure(
    campaign_id: str,
    person_id: str,
    error: str,
    org_id: str | None = None,
) -> None:
    c = load(campaign_id, org_id)
    if not c:
        return
    for r in c.recipients:
        if r.get("person_id") == person_id:
            r["status"] = "failed"
            r["error"] = error[:500]
            break
    c.failed_count += 1
    total = len(c.recipients)
    if c.sent_count + c.failed_count >= total and c.status == CampaignStatus.SENDING:
        c.transition_to(CampaignStatus.COMPLETED)
        redis_client.revoke_token(c.approval_token, org_id=c.org_id)
    save(c)
    redis_client.publish_event(c.campaign_id, {
        "type": "message.failed",
        "campaign_id": c.campaign_id,
        "person_id": person_id,
        "error": error[:200],
        "failed_count": c.failed_count,
        "total": total,
    }, org_id=c.org_id)


def increment_reply_count(campaign_id: str, org_id: str | None = None, *, person_id: str | None = None) -> None:
    """Record that an inbound reply arrived for this campaign.

    If person_id is known, mark that specific recipient as replied so future
    sends to them in this campaign are auto-skipped (prevents multi-send
    embarrassment — Stefan's explicit request).
    """
    c = load(campaign_id, org_id)
    if not c:
        return
    c.reply_count += 1
    if person_id:
        for r in c.recipients:
            if r.get("person_id") == person_id:
                r["reply_received"] = True
                break
    save(c)
    redis_client.publish_event(c.campaign_id, {
        "type": "message.reply_received",
        "campaign_id": c.campaign_id,
        "person_id": person_id,
        "reply_count": c.reply_count,
    }, org_id=c.org_id)
