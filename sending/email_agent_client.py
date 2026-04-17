"""
sending/email_agent_client.py — HTTP client to mla-email-agent.

Only entry point: POST /api/internal/send-now (Bearer EMAIL_AGENT_INTERNAL_KEY).
The CRM never talks to Gmail directly. Email-agent is the ONLY service that
holds Gmail credentials.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

import config

logger = logging.getLogger(__name__)


class SendNowError(RuntimeError):
    pass


async def send_now(
    *,
    campaign_id: str,
    approval_token: str,
    to: str,
    subject: str,
    body: str,
    cc: list[str] | None = None,
    attachment_urls: list[str] | None = None,
    recipient_person_id: str | None = None,
) -> dict[str, Any]:
    """Invoke email-agent to actually send one email. Returns {gmail_message_id, gmail_thread_id}."""
    url = config.EMAIL_AGENT_URL.rstrip("/") + "/api/internal/send-now"
    headers = {"Authorization": f"Bearer {config.EMAIL_AGENT_INTERNAL_KEY}"}
    payload = {
        "campaign_id": campaign_id,
        "approval_token": approval_token,
        "to": to,
        "subject": subject,
        "body": body,
        "cc": cc or [],
        "attachment_urls": attachment_urls or [],
        "recipient_person_id": recipient_person_id,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise SendNowError(
                f"email-agent returned {resp.status_code}: {resp.text[:500]}"
            )
        data = resp.json()
        if not data.get("sent"):
            raise SendNowError(f"email-agent refused: {data}")
        return {
            "gmail_message_id": data.get("gmail_message_id", ""),
            "gmail_thread_id": data.get("gmail_thread_id", ""),
        }
