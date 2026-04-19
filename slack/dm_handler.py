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
    files = event.get("files", []) or []
    channel = event.get("channel", "")

    # ── Auto-extract text from non-voice attachments ─────────────────────────
    # When Stefan drops a PDF / DOCX / XLSX / PPTX / image into the DM, pull
    # its content through mla-doc-reader and prepend to `text` so the CRM
    # brain sees the attachment body as conversation context.
    non_voice_files = [
        f for f in files
        if "audio" not in (f.get("mimetype") or "").lower()
        and f.get("subtype", "") != "slack_audio"
        and f.get("mode") != "voice"
    ]
    if non_voice_files:
        try:
            import asyncio
            from shared.doc_reader import read_slack_file
            bot_token = getattr(config, "SLACK_BOT_TOKEN", "") or ""
            doc_blocks: list[str] = []
            for f in non_voice_files:
                name = f.get("name", "") or f.get("title", "") or "(unnamed file)"
                try:
                    res = await asyncio.to_thread(
                        read_slack_file, f, bot_token,
                    )
                except Exception as e:
                    logger.warning(f"doc-reader call failed for {name}: {e}")
                    res = {"ok": False, "error": str(e)}
                if res.get("ok"):
                    body = (res.get("text") or "").strip()
                    if body:
                        doc_blocks.append(
                            f"--- Attached file: {name} ({res.get('char_count',0)} chars) ---\n{body}"
                        )
                    else:
                        doc_blocks.append(f"--- Attached file: {name} (no text extracted) ---")
                else:
                    err = res.get("error", "unknown")
                    doc_blocks.append(f"--- Attached file: {name} (doc-reader error: {err[:200]}) ---")
            if doc_blocks:
                prefix = "\n\n".join(doc_blocks)
                text = f"{prefix}\n\n{text}".strip() if text else prefix
        except Exception as e:
            logger.warning(f"shared.doc_reader unavailable: {e}")

    if not text:
        return

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
