"""/api/crm/campaigns/* — create, generate drafts, approve, pause/resume/cancel, status."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from airtable.schema import CampaignStatus
from airtable.segments import FilterValidationError
from api.deps import verify_crm_api_key, verify_crm_internal_key, get_org_id, envelope_ok
from domain import campaign as campaign_domain
from domain import segment as segment_domain
from domain import template as template_domain
from personalization.resolver import resolve_placeholders, find_unresolved_for_recipient
from sending import runner as send_runner
from sending.scheduler import will_exceed_daily_limit, now_utc
from slack import approval as approval_ui
from state import redis_client
from airtable import client as airtable_client
import config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/crm/campaigns", tags=["campaigns"])


# ── Schemas ────────────────────────────────────────────────────────────────

class CreateCampaignRequest(BaseModel):
    name: str
    template_name: str
    segment_name: str | None = None
    filter_json: dict[str, Any] | None = None
    instructions_override: str = ""   # optional single GPT tweak pass
    attachments: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    # ISO datetime string. If set, Stefan's Approve uses this as start time
    # (otherwise Approve starts sending immediately). Lets Stefan say
    # "zakaži slanje za 12. maja u 10h" and walk away.
    scheduled_start_at: str = ""


class ApproveRequest(BaseModel):
    slack_ts: str = ""
    # Optional override at approval time. If unset, falls back to the campaign's
    # own scheduled_start_at (set at creation), then to "now".
    scheduled_start_at: str = ""


class CustomSendRequest(BaseModel):
    name: str = "Custom send"
    subject: str
    body_template: str
    segment_name: str | None = None
    filter_json: dict[str, Any] | None = None
    attachments: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    scheduled_start_at: str = ""


# ── Helpers ────────────────────────────────────────────────────────────────

def _resolve_filter(segment_name: str | None, filter_json: dict | None) -> dict:
    if segment_name:
        seg = segment_domain.load_segment(segment_name)
        if not seg:
            raise HTTPException(404, f"Segment not found: {segment_name}")
        return seg["filter_json"]
    if filter_json is not None:
        return filter_json
    raise HTTPException(400, "Either segment_name or filter_json is required")


def _sender() -> dict:
    return {"name": config.DEFAULT_SENDER_NAME, "title": config.DEFAULT_SENDER_TITLE}


def _event() -> dict:
    return {"name": config.DEFAULT_EVENT_NAME}


def _maybe_llm_tweak(body: str, instructions: str) -> str:
    """Optional single GPT pass that tweaks the master body based on free-form instructions.

    Non-fatal: if OPENAI_API_KEY is absent or the call fails, returns original body.
    """
    if not instructions or not config.OPENAI_API_KEY:
        return body
    try:
        from openai import OpenAI
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": (
                    "You tweak an email master template body. Preserve all {{placeholders}} exactly. "
                    "Apply the user's instructions; keep tone consistent with the original. Output ONLY the new body."
                )},
                {"role": "user", "content": f"Original body:\n\n{body}\n\nInstructions:\n{instructions}"},
            ],
            max_tokens=1500,
        )
        new_body = resp.choices[0].message.content or body
        return new_body.strip()
    except Exception as e:
        logger.warning(f"LLM tweak failed (non-fatal): {e}")
        return body


def _render_recipients(
    master_subject: str,
    master_body: str,
    raw_recipients: list[dict],
) -> tuple[list[dict], list[str]]:
    """Render per-recipient drafts. Return (rendered_list, warnings_list)."""
    sender = _sender()
    event = _event()

    rendered = []
    warnings_seen: dict[str, int] = {}
    vvip_names: list[str] = []

    for r in raw_recipients:
        person = {
            "first_name": r.get("first_name", ""),
            "last_name": r.get("last_name", ""),
            "salutation": r.get("salutation", ""),
            "language": r.get("language", ""),
            "gender": r.get("gender", ""),
            "country": r.get("country", ""),
            "company_name": r.get("company_name", ""),
        }
        subject = resolve_placeholders(master_subject, person, sender, event)
        body = resolve_placeholders(master_body, person, sender, event)

        gaps = find_unresolved_for_recipient(master_body, person)
        for g in gaps:
            warnings_seen[g] = warnings_seen.get(g, 0) + 1
        if r.get("vip_flag"):
            vvip_names.append(f"{r.get('first_name','')} {r.get('last_name','')}".strip() or r.get("email", ""))

        rendered.append({
            "person_id": r["airtable_id"],
            "source_table": r.get("source_table", ""),
            "email": r["email"],
            "person_name": f"{r.get('first_name','')} {r.get('last_name','')}".strip(),
            "rendered_subject": subject,
            "rendered_body": body,
            "scheduled_send_at": "",   # filled by scheduler at approval time
            "status": "scheduled",
        })

    warnings: list[str] = []
    for field, count in warnings_seen.items():
        warnings.append(f"{count} recipient(s) missing `{field}` — placeholder renders empty")
    if vvip_names:
        warnings.append(f"VIP recipients ({len(vvip_names)}): {', '.join(vvip_names[:5])}"
                        + (" ..." if len(vvip_names) > 5 else ""))
    return rendered, warnings


# ── Routes ─────────────────────────────────────────────────────────────────

@router.post("")
async def create_and_generate(
    req: CreateCampaignRequest,
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    """Create campaign, resolve segment, generate previews, post to Slack — atomic."""
    tmpl = template_domain.load_template(req.template_name)
    if not tmpl:
        raise HTTPException(404, f"Template not found: {req.template_name}")

    try:
        filter_json = _resolve_filter(req.segment_name, req.filter_json)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))

    try:
        raw_recipients = segment_domain.recipients_for_filter(filter_json)
    except FilterValidationError as e:
        raise HTTPException(400, str(e))

    if not raw_recipients:
        raise HTTPException(400, "Segment matched 0 recipients with non-empty email")

    # Daily limit check
    already = airtable_client.count_sent_today()
    if will_exceed_daily_limit(len(raw_recipients), already, daily_limit=config.GMAIL_DAILY_LIMIT):
        raise HTTPException(
            429,
            f"Would exceed Gmail daily limit: {already} sent + {len(raw_recipients)} planned > {config.GMAIL_DAILY_LIMIT}. Split across days.",
        )

    # Optional GPT tweak of master body
    master_subject = tmpl["subject_template"] or "Regarding {{event_name}}"
    master_body = _maybe_llm_tweak(tmpl["body_template"], req.instructions_override)

    rendered, warnings = _render_recipients(master_subject, master_body, raw_recipients)

    c = campaign_domain.Campaign(
        campaign_id=campaign_domain.new_campaign_id(),
        name=req.name,
        template_name=req.template_name,
        segment_filter_json=filter_json,
        master_subject=master_subject,
        master_body=master_body,
        attachments=req.attachments,
        recipients=rendered,
        status=CampaignStatus.DRAFTING,
        scheduled_start_at=req.scheduled_start_at,
        org_id=org_id,
    )
    c.transition_to(CampaignStatus.AWAITING_APPROVAL)
    campaign_domain.save(c)

    # Post Slack approval
    previews = [
        {"email": r["email"], "body": r["rendered_body"]}
        for r in rendered[:3]
    ]
    try:
        ts = await approval_ui.post_approval_message(c, previews, warnings)
        c.approved_slack_ts = ts  # not yet approved, but tracking which message
        campaign_domain.save(c)
    except Exception as e:
        logger.warning(f"Slack approval post failed (campaign still awaiting): {e}")

    return envelope_ok({
        "campaign_id": c.campaign_id,
        "status": c.status,
        "recipient_count": len(rendered),
        "warnings": warnings,
        "previews": previews,
    })


@router.post("/{campaign_id}/approve")
async def approve(
    campaign_id: str,
    req: ApproveRequest = ApproveRequest(),
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    c = campaign_domain.load(campaign_id, org_id)
    if not c:
        raise HTTPException(404, "Campaign not found")
    if c.status != CampaignStatus.AWAITING_APPROVAL:
        raise HTTPException(409, f"Campaign status is {c.status}, cannot approve")
    c = campaign_domain.approve(campaign_id, slack_ts=req.slack_ts, org_id=org_id)

    # Resolve start_at: approval override > campaign-creation setting > now.
    start_at = _resolve_scheduled_start(req.scheduled_start_at, c.scheduled_start_at)
    send_runner.schedule_campaign(c, start_at=start_at)
    return envelope_ok({
        "campaign_id": c.campaign_id,
        "status": c.status,
        "approval_token_set": True,
        "scheduled_start_at": start_at.isoformat(),
    })


def _resolve_scheduled_start(approval_override: str, campaign_default: str):
    """Pick the start datetime: approval-time override > campaign-creation > now."""
    from datetime import datetime, timezone
    for candidate in (approval_override, campaign_default):
        if not candidate:
            continue
        try:
            dt = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            # Reject past times — silently round up to now so Stefan doesn't
            # lose a campaign if he mistyped the date.
            if dt < now_utc():
                return now_utc()
            return dt
        except (ValueError, TypeError):
            continue
    return now_utc()


@router.post("/{campaign_id}/pause")
async def pause(
    campaign_id: str,
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    c = campaign_domain.load(campaign_id, org_id)
    if not c:
        raise HTTPException(404, "Campaign not found")
    removed = send_runner.pause_campaign(c)
    try:
        c.transition_to(CampaignStatus.PAUSED)
    except ValueError:
        pass
    campaign_domain.save(c)
    return envelope_ok({"removed_jobs": removed, "status": c.status})


@router.post("/{campaign_id}/resume")
async def resume(
    campaign_id: str,
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    c = campaign_domain.load(campaign_id, org_id)
    if not c:
        raise HTTPException(404, "Campaign not found")
    if c.status != CampaignStatus.PAUSED:
        raise HTTPException(409, f"Cannot resume from status {c.status}")
    try:
        c.transition_to(CampaignStatus.SENDING)
    except ValueError:
        pass
    campaign_domain.save(c)
    added = send_runner.resume_campaign(c)
    return envelope_ok({"rescheduled_jobs": added, "status": c.status})


@router.post("/{campaign_id}/cancel")
async def cancel(
    campaign_id: str,
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    c = campaign_domain.load(campaign_id, org_id)
    if not c:
        raise HTTPException(404, "Campaign not found")
    removed = send_runner.cancel_campaign(c)
    try:
        c.transition_to(CampaignStatus.CANCELLED)
    except ValueError:
        pass
    campaign_domain.save(c)
    campaign_domain.revoke_token(campaign_id, org_id=org_id)
    return envelope_ok({"removed_jobs": removed, "status": c.status})


@router.get("/{campaign_id}/status")
async def status(
    campaign_id: str,
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    c = campaign_domain.load(campaign_id, org_id)
    if not c:
        raise HTTPException(404, "Campaign not found")
    return envelope_ok({
        "campaign_id": c.campaign_id,
        "name": c.name,
        "status": c.status,
        "recipient_count": len(c.recipients),
        "sent_count": c.sent_count,
        "failed_count": c.failed_count,
        "reply_count": c.reply_count,
        "scheduled_start_at": c.scheduled_start_at,
        "completed_at": c.completed_at,
    })


@router.get("/{campaign_id}/verify-token")
async def verify_token(
    campaign_id: str,
    token: str,
    _auth: str = Depends(verify_crm_internal_key),
    org_id: str = Depends(get_org_id),
):
    """Called by email-agent BEFORE each send to confirm approval is still valid."""
    ok = campaign_domain.verify_token(campaign_id, token, org_id=org_id)
    if not ok:
        return {"valid": False}
    c = campaign_domain.load(campaign_id, org_id)
    return {
        "valid": True,
        "campaign_status": c.status if c else None,
        "approved_by": "stefan",
    }


@router.get("/{campaign_id}/events")
async def events(
    campaign_id: str,
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    """SSE stream of campaign lifecycle events. Subscribes to Redis pubsub."""
    async def event_stream():
        client = redis_client.get_client()
        pubsub = client.pubsub()
        channel = redis_client.k_events_channel(campaign_id, org_id)
        pubsub.subscribe(channel)
        try:
            yield "event: open\ndata: {}\n\n"
            while True:
                msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=20.0)
                if msg is None:
                    # Heartbeat keeps proxies from closing the connection
                    yield ": heartbeat\n\n"
                    continue
                data = msg.get("data", "")
                yield f"data: {data}\n\n"
                await asyncio.sleep(0)
        finally:
            try:
                pubsub.unsubscribe(channel)
                pubsub.close()
            except Exception:
                pass

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/custom-send")
async def custom_send(
    req: CustomSendRequest,
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    """Skip AI drafting. Stefan supplies the body himself (with placeholders)."""
    try:
        filter_json = _resolve_filter(req.segment_name, req.filter_json)
    except HTTPException:
        raise

    try:
        raw_recipients = segment_domain.recipients_for_filter(filter_json)
    except FilterValidationError as e:
        raise HTTPException(400, str(e))

    if not raw_recipients:
        raise HTTPException(400, "Segment matched 0 recipients")

    already = airtable_client.count_sent_today()
    if will_exceed_daily_limit(len(raw_recipients), already, daily_limit=config.GMAIL_DAILY_LIMIT):
        raise HTTPException(429, "Would exceed Gmail daily limit. Split across days.")

    rendered, warnings = _render_recipients(req.subject, req.body_template, raw_recipients)

    c = campaign_domain.Campaign(
        campaign_id=campaign_domain.new_campaign_id(),
        name=req.name,
        template_name="(custom)",
        segment_filter_json=filter_json,
        master_subject=req.subject,
        master_body=req.body_template,
        attachments=req.attachments,
        recipients=rendered,
        status=CampaignStatus.DRAFTING,
        scheduled_start_at=req.scheduled_start_at,
        org_id=org_id,
    )
    c.transition_to(CampaignStatus.AWAITING_APPROVAL)
    campaign_domain.save(c)

    previews = [{"email": r["email"], "body": r["rendered_body"]} for r in rendered[:3]]
    try:
        ts = await approval_ui.post_approval_message(c, previews, warnings)
        c.approved_slack_ts = ts
        campaign_domain.save(c)
    except Exception as e:
        logger.warning(f"Slack approval post failed: {e}")

    return envelope_ok({
        "campaign_id": c.campaign_id,
        "status": c.status,
        "recipient_count": len(rendered),
        "warnings": warnings,
        "previews": previews,
    })
