"""
slack/approval.py — Build the single-approval Block Kit message for a campaign.

Stefan sees ONE Slack message per campaign:
- master subject
- 3 rendered previews (real recipients)
- recipient count + schedule window
- warnings for data gaps (missing salutation, VVIP recipients, etc.)
- 4 buttons: Approve / Edit template / Edit list / Cancel
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from slack_sdk.web.async_client import AsyncWebClient

import config
from domain.campaign import Campaign
from sending.scheduler import format_schedule_summary


def build_approval_blocks(
    campaign: Campaign,
    previews: list[dict[str, str]],
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Return the Block Kit block list."""
    total = len(campaign.recipients)

    # Schedule summary from scheduled_send_at on recipients
    fires = []
    for r in campaign.recipients:
        ts = r.get("scheduled_send_at", "")
        if ts:
            try:
                fires.append(datetime.fromisoformat(ts))
            except ValueError:
                pass
    summary = format_schedule_summary(fires) if fires else {"gap_min_s": 0, "gap_max_s": 0, "first": None, "last": None}

    header = f"📣 *Campaign: {campaign.name}*"
    recipients_line = f"*{total} recipients* · template: `{campaign.template_name}`"

    schedule_line = ""
    # If a future start is requested at creation time, show that up-front.
    if campaign.scheduled_start_at:
        try:
            future = datetime.fromisoformat(campaign.scheduled_start_at.replace("Z", "+00:00"))
            schedule_line = (
                f"📅 Scheduled start: *{future.strftime('%Y-%m-%d %H:%M')}* ({config.TIMEZONE}). "
                f"Approve now → sends will begin at that time."
            )
        except ValueError:
            pass

    if not schedule_line and summary.get("first") and summary.get("last"):
        first_dt = datetime.fromisoformat(summary["first"])
        last_dt = datetime.fromisoformat(summary["last"])
        gap_min_min = summary["gap_min_s"] // 60
        gap_max_min = summary["gap_max_s"] // 60
        schedule_line = (
            f"⏱ {total} sends, ~{gap_min_min}–{gap_max_min} min gaps · "
            f"finishes {last_dt.strftime('%Y-%m-%d %H:%M')} ({config.TIMEZONE})"
        )

    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": "CRM Campaign — approval required"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
        {"type": "section", "text": {"type": "mrkdwn", "text": recipients_line}},
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Subject:*\n{_escape(campaign.master_subject)}"},
        },
    ]

    # Previews
    if previews:
        preview_text = "*Previews (first 3 recipients):*\n"
        for i, p in enumerate(previews[:3], 1):
            preview_text += f"\n*{i}. → {_escape(p.get('email',''))}*\n{_escape(p.get('body',''))[:500]}\n"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": preview_text}})

    if warnings:
        warn_text = "⚠️ *Warnings:*\n" + "\n".join(f"• {_escape(w)}" for w in warnings[:6])
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": warn_text}})

    if schedule_line:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": schedule_line}]})

    blocks.append({"type": "divider"})

    blocks.append({
        "type": "actions",
        "block_id": f"crm_actions_{campaign.campaign_id}",
        "elements": [
            {
                "type": "button",
                "style": "primary",
                "text": {"type": "plain_text", "text": f"✅ Approve all {total}"},
                "action_id": "crm_approve",
                "value": campaign.campaign_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "📝 Edit template"},
                "action_id": "crm_edit_template",
                "value": campaign.campaign_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "✂ Edit list"},
                "action_id": "crm_edit_list",
                "value": campaign.campaign_id,
            },
            {
                "type": "button",
                "style": "danger",
                "text": {"type": "plain_text", "text": "❌ Cancel"},
                "action_id": "crm_cancel",
                "value": campaign.campaign_id,
            },
        ],
    })

    return blocks


def _escape(text: str) -> str:
    """Minimal Slack mrkdwn escaping."""
    if not text:
        return ""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


async def post_approval_message(campaign: Campaign, previews: list[dict], warnings: list[str]) -> str:
    """Post the approval message to Stefan's DM. Returns message_ts."""
    if not config.SLACK_BOT_TOKEN or not config.SLACK_DEFAULT_CHANNEL:
        raise RuntimeError(
            "Slack not configured. Set SLACK_BOT_TOKEN and SLACK_DEFAULT_CHANNEL env vars."
        )
    client = AsyncWebClient(token=config.SLACK_BOT_TOKEN)
    blocks = build_approval_blocks(campaign, previews, warnings)
    resp = await client.chat_postMessage(
        channel=config.SLACK_DEFAULT_CHANNEL,
        text=f"CRM campaign ready: {campaign.name} ({len(campaign.recipients)} recipients)",
        blocks=blocks,
    )
    return resp.get("ts", "")


async def post_progress_update(campaign: Campaign, text: str) -> None:
    if not config.SLACK_BOT_TOKEN or not config.SLACK_DEFAULT_CHANNEL:
        return
    client = AsyncWebClient(token=config.SLACK_BOT_TOKEN)
    await client.chat_postMessage(
        channel=config.SLACK_DEFAULT_CHANNEL,
        text=text,
    )


async def update_message(channel: str, ts: str, new_text: str) -> None:
    if not config.SLACK_BOT_TOKEN:
        return
    client = AsyncWebClient(token=config.SLACK_BOT_TOKEN)
    await client.chat_update(channel=channel, ts=ts, text=new_text, blocks=[])
