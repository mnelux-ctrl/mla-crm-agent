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
    """email-agent posts here when an inbound message arrives on a CRM campaign thread."""
    campaign_domain.increment_reply_count(req.campaign_id)
    return envelope_ok({"campaign_id": req.campaign_id})
