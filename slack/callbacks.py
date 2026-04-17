"""
slack/callbacks.py — Slack interactivity handlers (Approve / Edit / Cancel).

Register these with a Slack Bolt async app in main.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import config
from domain import campaign as campaign_domain
from airtable.schema import CampaignStatus
from sending import runner as send_runner
from slack import approval as approval_ui

logger = logging.getLogger(__name__)


def _is_stefan(user_id: str) -> bool:
    if not config.SLACK_STEFAN_USER_ID:
        # If not configured, be permissive — but warn in logs.
        logger.warning("SLACK_STEFAN_USER_ID not set; accepting any Slack user for CRM actions")
        return True
    return user_id == config.SLACK_STEFAN_USER_ID


async def handle_approve(body: dict, ack, client) -> None:
    await ack()
    user = body.get("user", {}).get("id", "")
    if not _is_stefan(user):
        logger.warning(f"Non-Stefan user {user} tried to approve campaign")
        return

    action = body.get("actions", [{}])[0]
    campaign_id = action.get("value", "")
    message = body.get("message", {})
    ts = message.get("ts", "")
    channel = body.get("channel", {}).get("id", "")

    c = campaign_domain.load(campaign_id)
    if not c:
        await client.chat_postMessage(channel=channel, text=f"⚠️ Campaign {campaign_id} not found.")
        return

    if c.status != CampaignStatus.AWAITING_APPROVAL:
        await client.chat_postMessage(
            channel=channel,
            text=f"⚠️ Campaign `{c.name}` is `{c.status}`, cannot approve.",
        )
        return

    try:
        c = campaign_domain.approve(campaign_id, slack_ts=ts)
    except Exception as e:
        logger.exception("approve failed")
        await client.chat_postMessage(channel=channel, text=f"⚠️ Approve failed: {e}")
        return

    # Schedule sends
    from sending.scheduler import now_utc
    scheduled = send_runner.schedule_campaign(c, start_at=now_utc())
    total = len(c.recipients)

    await client.chat_update(
        channel=channel, ts=ts,
        text=(
            f"✅ *Approved* — {c.name}\n"
            f"Scheduling {scheduled}/{total} sends now. "
            f"You'll get progress updates every 10 messages."
        ),
        blocks=[],
    )


async def handle_cancel(body: dict, ack, client) -> None:
    await ack()
    user = body.get("user", {}).get("id", "")
    if not _is_stefan(user):
        return

    action = body.get("actions", [{}])[0]
    campaign_id = action.get("value", "")
    ts = body.get("message", {}).get("ts", "")
    channel = body.get("channel", {}).get("id", "")

    c = campaign_domain.load(campaign_id)
    if not c:
        return

    # Cancel pending jobs
    removed = send_runner.cancel_campaign(c)

    try:
        c.transition_to(CampaignStatus.CANCELLED)
    except ValueError:
        pass  # already terminal
    campaign_domain.save(c)
    campaign_domain.revoke_token(campaign_id)

    await client.chat_update(
        channel=channel, ts=ts,
        text=f"❌ *Cancelled* — {c.name} ({removed} pending sends removed)",
        blocks=[],
    )


async def handle_edit_template(body: dict, ack, client) -> None:
    await ack()
    # MVP: point Stefan to Airtable to edit the template, then regenerate
    ts = body.get("message", {}).get("ts", "")
    channel = body.get("channel", {}).get("id", "")
    action = body.get("actions", [{}])[0]
    campaign_id = action.get("value", "")
    await client.chat_postMessage(
        channel=channel,
        thread_ts=ts,
        text=(
            f"📝 To edit the template: open Airtable CRM_TEMPLATE, modify, then "
            f"say to COO: *'regeneriši campaign {campaign_id}'*"
        ),
    )


async def handle_edit_list(body: dict, ack, client) -> None:
    await ack()
    ts = body.get("message", {}).get("ts", "")
    channel = body.get("channel", {}).get("id", "")
    action = body.get("actions", [{}])[0]
    campaign_id = action.get("value", "")
    c = campaign_domain.load(campaign_id)
    if not c:
        return
    emails = "\n".join(f"• {r['email']}" for r in c.recipients[:50])
    rest = ""
    if len(c.recipients) > 50:
        rest = f"\n_... +{len(c.recipients) - 50} more_"
    await client.chat_postMessage(
        channel=channel,
        thread_ts=ts,
        text=f"📋 Recipients for `{c.name}`:\n{emails}{rest}\n\n(Full list editing UI will come with the frontend.)",
    )
