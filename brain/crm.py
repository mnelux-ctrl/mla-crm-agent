"""
brain/crm.py — CRM Agent's LLM reasoning loop.

Lets Stefan talk to the CRM agent directly (via a Slack DM bot) in plain
language. GPT plans + calls tools (from brain.tools.TOOLS) that point at the
same domain functions the HTTP API exposes — so a Slack command is equivalent
to an API call on the back end.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from openai import OpenAI

import config
from airtable import client as airtable_client
from airtable.segments import FilterValidationError
from brain.prompts import SYSTEM_PROMPT
from brain.tools import TOOLS
from domain import campaign as campaign_domain
from domain import segment as segment_domain
from domain import template as template_domain
from personalization.resolver import resolve_placeholders, find_unresolved_for_recipient
from sending import runner as send_runner
from sending.scheduler import now_utc, will_exceed_daily_limit
from slack import approval as approval_ui
from state import redis_client

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8
CONVERSATION_TTL_SECONDS = 4 * 3600  # 4h


_openai: OpenAI | None = None


def _get_openai() -> OpenAI:
    global _openai
    if _openai is None:
        if not config.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not configured — CRM brain disabled.")
        _openai = OpenAI(api_key=config.OPENAI_API_KEY)
    return _openai


# ── Conversation memory (per Slack channel) ─────────────────────────────────

def _conv_key(channel: str) -> str:
    return f"crm:conv:{channel}"


def _load_conversation(channel: str) -> list[dict]:
    client = redis_client.get_client()
    raw = client.get(_conv_key(channel))
    return json.loads(raw) if raw else []


def _save_conversation(channel: str, messages: list[dict]) -> None:
    client = redis_client.get_client()
    # Keep last 20 turns to cap tokens
    trimmed = messages[-40:]
    client.set(_conv_key(channel), json.dumps(trimmed), ex=CONVERSATION_TTL_SECONDS)


def clear_conversation(channel: str) -> None:
    client = redis_client.get_client()
    client.delete(_conv_key(channel))


# ── Core loop ──────────────────────────────────────────────────────────────

async def process_message(user_message: str, channel: str, org_id: str = "mla") -> str:
    """Process a Stefan message, call tools as needed, return the final reply."""
    history = _load_conversation(channel)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [
        {"role": "user", "content": user_message}
    ]

    openai = _get_openai()
    final_response = ""

    for round_num in range(MAX_TOOL_ROUNDS):
        resp = openai.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        msg = resp.choices[0].message

        if not msg.tool_calls:
            final_response = msg.content or "(no response)"
            messages.append({"role": "assistant", "content": final_response})
            break

        # Append the assistant's tool-calling turn
        messages.append({
            "role": "assistant",
            "content": msg.content or None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ],
        })

        # Execute each tool call
        for tc in msg.tool_calls:
            tool_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {}
            logger.info(f"CRM brain: tool_call {tool_name} args={args}")
            result = await _execute_tool(tool_name, args, channel=channel, org_id=org_id)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, default=str)[:6000],
            })
    else:
        final_response = "⚠️ Dostigao sam maksimum tool koraka. Možda pitanje zahteva da ga razbijemo."

    # Save back minus the system prompt
    user_and_later = [m for m in messages if m.get("role") != "system"]
    _save_conversation(channel, user_and_later)
    return final_response


# ── Tool dispatcher ────────────────────────────────────────────────────────

async def _execute_tool(name: str, args: dict, *, channel: str, org_id: str) -> dict:
    """Run a tool locally and return its result dict."""
    try:
        if name == "list_templates":
            rows = template_domain.list_templates()
            return {"ok": True, "templates": [
                {"name": r["name"], "channel": r["channel"], "language": r["language"],
                 "partner_category": r["partner_category"]}
                for r in rows
            ]}

        if name == "list_segments":
            rows = segment_domain.list_segments()
            return {"ok": True, "segments": [
                {"name": r["name"], "description": r["description"],
                 "filter_json": r["filter_json"]}
                for r in rows
            ]}

        if name == "preview_segment":
            segment_name = args.get("segment_name") or ""
            filter_json = args.get("filter_json")
            if segment_name:
                seg = segment_domain.load_segment(segment_name)
                if not seg:
                    return {"ok": False, "error": f"Segment not found: {segment_name}"}
                filter_json = seg["filter_json"]
            if not filter_json:
                return {"ok": False, "error": "segment_name or filter_json required"}
            try:
                result = segment_domain.preview_segment(filter_json, limit=5)
                return {"ok": True, **result}
            except FilterValidationError as e:
                return {"ok": False, "error": str(e)}

        if name == "launch_campaign":
            return await _launch_campaign(args, org_id=org_id)

        if name == "custom_send":
            return await _custom_send(args, org_id=org_id)

        if name == "campaign_status":
            cid = args.get("campaign_id", "")
            c = campaign_domain.load(cid, org_id)
            if not c:
                return {"ok": False, "error": f"Campaign not found: {cid}"}
            return {"ok": True, "campaign_id": c.campaign_id, "name": c.name,
                    "status": c.status, "recipient_count": len(c.recipients),
                    "sent": c.sent_count, "failed": c.failed_count, "replies": c.reply_count,
                    "scheduled_start_at": c.scheduled_start_at, "completed_at": c.completed_at}

        if name == "pause_campaign":
            return _pause_campaign(args, org_id=org_id)

        if name == "resume_campaign":
            return _resume_campaign(args, org_id=org_id)

        if name == "cancel_campaign":
            return _cancel_campaign(args, org_id=org_id)

        if name == "save_segment":
            try:
                out = segment_domain.save_segment(args)
                return {"ok": True, "saved": out["name"]}
            except (ValueError, FilterValidationError) as e:
                return {"ok": False, "error": str(e)}

        if name == "delegate_to_admin":
            return await _delegate_to_admin(args)

        if name == "recall_knowledge":
            return await _recall_knowledge(args)

        return {"ok": False, "error": f"Unknown tool: {name}"}

    except Exception as e:
        logger.exception(f"Tool {name} crashed")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def _launch_campaign(args: dict, *, org_id: str) -> dict:
    from airtable.schema import CampaignStatus
    tmpl = template_domain.load_template(args.get("template_name", ""))
    if not tmpl:
        return {"ok": False, "error": f"Template not found: {args.get('template_name')}"}

    filter_json = _resolve_filter(args)
    if not filter_json:
        return {"ok": False, "error": "segment_name or filter_json required"}

    try:
        raw = segment_domain.recipients_for_filter(filter_json)
    except FilterValidationError as e:
        return {"ok": False, "error": str(e)}

    if not raw:
        return {"ok": False, "error": "Segment matched 0 recipients with email"}

    already = airtable_client.count_sent_today()
    if will_exceed_daily_limit(len(raw), already, daily_limit=config.GMAIL_DAILY_LIMIT):
        return {"ok": False, "error": f"Would exceed Gmail daily limit ({config.GMAIL_DAILY_LIMIT}). Split across days."}

    master_subject = tmpl["subject_template"] or "Regarding {{event_name}}"
    master_body = tmpl["body_template"]
    instructions = args.get("instructions_override", "")
    if instructions and config.OPENAI_API_KEY:
        master_body = _tweak_body(master_body, instructions)

    rendered, warnings = _render(master_subject, master_body, raw)

    c = campaign_domain.Campaign(
        campaign_id=campaign_domain.new_campaign_id(),
        name=args.get("name") or f"Campaign via {args.get('template_name')}",
        template_name=args.get("template_name", ""),
        segment_filter_json=filter_json,
        master_subject=master_subject,
        master_body=master_body,
        attachments=args.get("attachments", []) or [],
        recipients=rendered,
        status=CampaignStatus.DRAFTING,
        scheduled_start_at=args.get("scheduled_start_at", ""),
        org_id=org_id,
    )
    c.transition_to(CampaignStatus.AWAITING_APPROVAL)
    campaign_domain.save(c)

    previews = [{"email": r["email"], "body": r["rendered_body"]} for r in rendered[:3]]
    slack_ts = ""
    try:
        slack_ts = await approval_ui.post_approval_message(c, previews, warnings)
        c.approved_slack_ts = slack_ts
        campaign_domain.save(c)
    except Exception as e:
        logger.warning(f"Slack approval post failed (campaign still awaiting): {e}")

    return {
        "ok": True, "campaign_id": c.campaign_id, "status": c.status,
        "recipient_count": len(rendered), "warnings": warnings,
        "preview_examples": [p["email"] for p in previews],
        "scheduled_start_at": c.scheduled_start_at,
        "slack_ts": slack_ts,
    }


async def _custom_send(args: dict, *, org_id: str) -> dict:
    from airtable.schema import CampaignStatus
    filter_json = _resolve_filter(args)
    if not filter_json:
        return {"ok": False, "error": "segment_name or filter_json required"}
    try:
        raw = segment_domain.recipients_for_filter(filter_json)
    except FilterValidationError as e:
        return {"ok": False, "error": str(e)}
    if not raw:
        return {"ok": False, "error": "Segment matched 0 recipients"}

    already = airtable_client.count_sent_today()
    if will_exceed_daily_limit(len(raw), already, daily_limit=config.GMAIL_DAILY_LIMIT):
        return {"ok": False, "error": f"Would exceed Gmail daily limit"}

    rendered, warnings = _render(args.get("subject", ""), args.get("body_template", ""), raw)

    c = campaign_domain.Campaign(
        campaign_id=campaign_domain.new_campaign_id(),
        name=args.get("name", "Custom send"),
        template_name="(custom)",
        segment_filter_json=filter_json,
        master_subject=args.get("subject", ""),
        master_body=args.get("body_template", ""),
        recipients=rendered,
        status=CampaignStatus.DRAFTING,
        scheduled_start_at=args.get("scheduled_start_at", ""),
        org_id=org_id,
    )
    c.transition_to(CampaignStatus.AWAITING_APPROVAL)
    campaign_domain.save(c)

    previews = [{"email": r["email"], "body": r["rendered_body"]} for r in rendered[:3]]
    try:
        slack_ts = await approval_ui.post_approval_message(c, previews, warnings)
        c.approved_slack_ts = slack_ts
        campaign_domain.save(c)
    except Exception as e:
        logger.warning(f"Slack approval post failed: {e}")

    return {"ok": True, "campaign_id": c.campaign_id, "status": c.status,
            "recipient_count": len(rendered), "warnings": warnings}


def _pause_campaign(args: dict, org_id: str) -> dict:
    from airtable.schema import CampaignStatus
    c = campaign_domain.load(args.get("campaign_id", ""), org_id)
    if not c:
        return {"ok": False, "error": "campaign not found"}
    removed = send_runner.pause_campaign(c)
    try:
        c.transition_to(CampaignStatus.PAUSED)
    except ValueError:
        pass
    campaign_domain.save(c)
    return {"ok": True, "removed_jobs": removed, "status": c.status}


def _resume_campaign(args: dict, org_id: str) -> dict:
    from airtable.schema import CampaignStatus
    c = campaign_domain.load(args.get("campaign_id", ""), org_id)
    if not c:
        return {"ok": False, "error": "campaign not found"}
    if c.status != CampaignStatus.PAUSED:
        return {"ok": False, "error": f"cannot resume from status {c.status}"}
    try:
        c.transition_to(CampaignStatus.SENDING)
    except ValueError:
        pass
    campaign_domain.save(c)
    added = send_runner.resume_campaign(c)
    return {"ok": True, "rescheduled_jobs": added, "status": c.status}


def _cancel_campaign(args: dict, org_id: str) -> dict:
    from airtable.schema import CampaignStatus
    c = campaign_domain.load(args.get("campaign_id", ""), org_id)
    if not c:
        return {"ok": False, "error": "campaign not found"}
    removed = send_runner.cancel_campaign(c)
    try:
        c.transition_to(CampaignStatus.CANCELLED)
    except ValueError:
        pass
    campaign_domain.save(c)
    campaign_domain.revoke_token(c.campaign_id, org_id=org_id)
    return {"ok": True, "removed_jobs": removed, "status": c.status}


async def _delegate_to_admin(args: dict) -> dict:
    """Call the Admin Executor to produce a Google Doc."""
    admin_url = (
        __import__("os").environ.get("ADMIN_EXECUTOR_URL", "").rstrip("/")
    )
    admin_key = __import__("os").environ.get("ADMIN_API_KEY", "")
    if not admin_url or not admin_key:
        return {"ok": False, "error": "Admin Executor not configured (ADMIN_EXECUTOR_URL / ADMIN_API_KEY missing)"}
    headers = {"Authorization": f"Bearer {admin_key}"}
    payload = {
        "action": "draft_document",
        "document_type": args.get("document_type"),
        "instructions": args.get("instructions"),
        "context": args.get("context", ""),
    }
    url = f"{admin_url}/api/draft-document"
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            return {"ok": False, "error": f"admin returned {resp.status_code}: {resp.text[:200]}"}
        return resp.json()
    except Exception as e:
        return {"ok": False, "error": f"admin unreachable: {e}"}


async def _recall_knowledge(args: dict) -> dict:
    if not config.SUPERKNOWLEDGE_URL or not config.SUPERKNOWLEDGE_API_KEY:
        return {"ok": False, "error": "SuperKnowledge not configured"}
    url = f"{config.SUPERKNOWLEDGE_URL.rstrip('/')}/api/recall"
    headers = {"Authorization": f"Bearer {config.SUPERKNOWLEDGE_API_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json={"query": args.get("query", "")}, headers=headers)
        return resp.json() if resp.status_code == 200 else {"ok": False, "error": f"{resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _resolve_filter(args: dict) -> dict | None:
    if args.get("segment_name"):
        seg = segment_domain.load_segment(args["segment_name"])
        return seg["filter_json"] if seg else None
    return args.get("filter_json")


def _render(master_subject: str, master_body: str, raw: list[dict]):
    sender = {"name": config.DEFAULT_SENDER_NAME, "title": config.DEFAULT_SENDER_TITLE}
    event = {"name": config.DEFAULT_EVENT_NAME}
    rendered, warnings_seen, vvip = [], {}, []
    for r in raw:
        person = {
            "first_name": r.get("first_name", ""), "last_name": r.get("last_name", ""),
            "salutation": r.get("salutation", ""), "language": r.get("language", ""),
            "gender": r.get("gender", ""), "country": r.get("country", ""),
            "company_name": r.get("company_name", ""),
        }
        subject = resolve_placeholders(master_subject, person, sender, event)
        body = resolve_placeholders(master_body, person, sender, event)
        for g in find_unresolved_for_recipient(master_body, person):
            warnings_seen[g] = warnings_seen.get(g, 0) + 1
        if r.get("vip_flag"):
            vvip.append(f"{r.get('first_name','')} {r.get('last_name','')}".strip() or r.get("email", ""))
        rendered.append({
            "person_id": r["airtable_id"], "source_table": r.get("source_table", ""),
            "email": r["email"],
            "person_name": f"{r.get('first_name','')} {r.get('last_name','')}".strip(),
            "rendered_subject": subject, "rendered_body": body,
            "scheduled_send_at": "", "status": "scheduled",
        })
    warnings: list[str] = []
    for field, count in warnings_seen.items():
        warnings.append(f"{count} recipient(s) missing `{field}`")
    if vvip:
        warnings.append(f"VIP recipients ({len(vvip)}): {', '.join(vvip[:5])}"
                        + (" ..." if len(vvip) > 5 else ""))
    return rendered, warnings


def _tweak_body(body: str, instructions: str) -> str:
    try:
        openai = _get_openai()
        resp = openai.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": (
                    "You tweak an email master template body. Preserve all {{placeholders}} exactly. "
                    "Apply the user's instructions. Output ONLY the new body."
                )},
                {"role": "user", "content": f"Original:\n\n{body}\n\nInstructions:\n{instructions}"},
            ],
            max_tokens=1500,
        )
        return (resp.choices[0].message.content or body).strip()
    except Exception as e:
        logger.warning(f"body tweak failed (non-fatal): {e}")
        return body
