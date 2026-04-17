"""
api/internal.py — service-to-service callbacks.

Called by mla-email-agent after successful send (optional redundancy) and when
an inbound reply matches a CRM campaign thread.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import verify_crm_internal_key, envelope_ok
from domain import campaign as campaign_domain

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/internal", tags=["internal"])


class SendResultRequest(BaseModel):
    campaign_id: str
    person_id: str
    success: bool
    gmail_message_id: str = ""
    gmail_thread_id: str = ""
    error: str = ""


@router.post("/record-send-result")
async def record_send_result(
    req: SendResultRequest,
    _auth: str = Depends(verify_crm_internal_key),
):
    if req.success:
        campaign_domain.record_send_success(
            req.campaign_id, req.person_id,
            req.gmail_message_id, req.gmail_thread_id,
        )
    else:
        campaign_domain.record_send_failure(req.campaign_id, req.person_id, req.error)
    return envelope_ok()


class ReplyReceivedRequest(BaseModel):
    campaign_id: str
    person_id: str | None = None
    thread_id: str
    message_id: str
    received_at: str = ""


@router.post("/webhooks/reply-received")
async def reply_received(
    req: ReplyReceivedRequest,
    _auth: str = Depends(verify_crm_internal_key),
):
    """email-agent posts here when an inbound message arrives on a CRM campaign thread.

    Also marks the specific recipient as replied so any remaining scheduled
    sends to them in this campaign are auto-skipped.
    """
    campaign_domain.increment_reply_count(req.campaign_id, person_id=req.person_id)
    return envelope_ok({"campaign_id": req.campaign_id, "person_id": req.person_id})


class BounceRequest(BaseModel):
    recipient_email: str
    bounce_type: str = "hard"   # hard / soft
    reason: str = ""
    campaign_id: str | None = None


@router.post("/webhooks/bounce-detected")
async def bounce_detected(
    req: BounceRequest,
    _auth: str = Depends(verify_crm_internal_key),
):
    """email-agent posts here when a send bounces. We mark the contact's
    `do_not_contact` in Airtable on HARD bounce so future campaigns skip them.

    Soft bounces are logged but the contact is kept (temporary mailbox issues).
    """
    import logging
    from airtable import client as ac

    logger = logging.getLogger(__name__)
    logger.warning(
        f"Bounce: {req.bounce_type} for {req.recipient_email} (campaign={req.campaign_id}): {req.reason}"
    )

    marked = False
    if req.bounce_type == "hard":
        # Find contact across tables and mark do_not_contact
        try:
            contact, table = ac.fetch_contact_by_email(req.recipient_email)
            if contact and table:
                api = ac._get_api()
                import config as cfg
                tbl = api.table(cfg.AIRTABLE_BASE_ID, table)
                tbl.update(contact["id"], {"do_not_contact": True, "notes": f"Auto-flagged: {req.bounce_type} bounce on {req.recipient_email}. Reason: {req.reason[:200]}"}, typecast=True)
                marked = True
        except Exception as e:
            logger.error(f"Failed to mark bounced contact: {e}")

    return envelope_ok({
        "recipient_email": req.recipient_email,
        "bounce_type": req.bounce_type,
        "marked_do_not_contact": marked,
    })
