"""/api/debug/* — admin-only debug endpoints for testing the LLM brain without Slack.

These are gated by CRM_API_KEY (not CRM_INTERNAL_KEY) so only trusted callers
(COO, Stefan-authored scripts) can hit them. Useful for smoke tests + CI.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import verify_crm_api_key, get_org_id, envelope_ok
from brain import crm as crm_brain

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/debug", tags=["debug"])


class ChatRequest(BaseModel):
    text: str
    channel_id: str = "debug-cli"
    reset: bool = False


@router.post("/chat")
async def chat(
    req: ChatRequest,
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    """Send a message through the CRM brain. Returns the same reply a Slack DM would.

    Use this to verify the LLM's tool selection, Airtable calls, and tone without
    going through Slack's event pipeline. Conversation history is stored in Redis
    keyed on channel_id, so you can hold a multi-turn conversation.
    """
    if req.reset:
        crm_brain.clear_conversation(req.channel_id)
    response = await crm_brain.process_message(req.text, channel=req.channel_id, org_id=org_id)
    return envelope_ok({"reply": response, "channel_id": req.channel_id})
