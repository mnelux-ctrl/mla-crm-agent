"""
slack/dm_handler.py — Handle direct messages to the CRM Slack bot.

Stefan DMs the bot in plain language → bot calls GPT with CRM tools → replies
in the same thread. Exactly the pattern COO agent uses.
"""

from __future__ import annotations

import logging

import config
from brain import crm as crm_brain

logger = logging.getLogger(__name__)


def _is_stefan(user_id: str) -> bool:
    if not config.SLACK_STEFAN_USER_ID:
        logger.warning("SLACK_STEFAN_USER_ID not set — accepting any DM sender (DEV MODE)")
        return True
    return user_id == config.SLACK_STEFAN_USER_ID


async def handle_dm(event: dict, client) -> None:
    """Called on Slack `message` events in DMs (channel_type == 'im')."""
    # Ignore bot messages and edits to avoid loops
    if event.get("bot_id") or event.get("subtype") in ("bot_message", "message_changed", "message_deleted"):
        return

    user = event.get("user", "")
    if not _is_stefan(user):
        logger.info(f"Non-Stefan DM ignored (user={user})")
        return

    text = (event.get("text") or "").strip()
    if not text:
        return

    channel = event.get("channel", "")

    # Thinking indicator
    try:
        await client.chat_postMessage(channel=channel, text="⏳ Razmišljam...")
    except Exception:
        pass

    try:
        response = await crm_brain.process_message(text, channel=channel)
    except Exception as e:
        logger.exception("CRM brain error")
        response = f"⚠️ Greška u CRM brain-u: `{type(e).__name__}: {e}`"

    try:
        await client.chat_postMessage(channel=channel, text=response)
    except Exception as e:
        logger.error(f"chat_postMessage failed: {e}")


async def handle_clear(body: dict, ack, client) -> None:
    """Reset conversation when Stefan clicks 'Clear history'."""
    await ack()
    channel = body.get("channel", {}).get("id", "")
    crm_brain.clear_conversation(channel)
    try:
        await client.chat_postMessage(channel=channel, text="🧹 Conversation cleared.")
    except Exception:
        pass
