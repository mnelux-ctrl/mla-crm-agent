"""
api/coo.py — Single-call entry point used by mla-coo-agent.

COO GPT tool `delegate_crm_campaign` posts here and gets back a summary + Slack TS.
Everything a voice-triggered campaign needs in one round-trip.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import verify_crm_api_key, get_org_id, envelope_ok
from api.campaigns import CreateCampaignRequest, create_and_generate
from domain import segment as segment_domain
from domain import template as template_domain

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/coo", tags=["coo"])


class CooCampaignRequest(BaseModel):
    name: str = ""
    segment_name: str | None = None
    filter_json: dict[str, Any] | None = None
    template_name: str
    instructions_override: str = ""
    attachments: list[str] = Field(default_factory=list)
    save_segment_as: str = ""
    # ISO datetime string (e.g. "2026-05-12T10:00:00+02:00"). Empty = start as
    # soon as Stefan approves. Stefan says "zakaži za 25 dana u 10h" → COO
    # converts to absolute ISO and passes it here.
    scheduled_start_at: str = ""


@router.post("/crm-campaign")
async def coo_launch_campaign(
    req: CooCampaignRequest,
    auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    """Launch a campaign from a single voice-initiated COO call.

    Creates campaign + generates drafts + posts Slack approval in one shot.
    Optionally saves the filter_json as a named segment for reuse.
    """
    # Optionally save the ad-hoc filter for reuse
    if req.save_segment_as and req.filter_json:
        try:
            segment_domain.save_segment({
                "name": req.save_segment_as,
                "description": f"Saved by COO during campaign '{req.name}'",
                "filter_json": req.filter_json,
            })
        except Exception as e:
            logger.warning(f"save_segment_as soft-failed: {e}")

    fallback_name = req.name or f"COO campaign via {req.template_name}"
    inner = CreateCampaignRequest(
        name=fallback_name,
        template_name=req.template_name,
        segment_name=req.segment_name,
        filter_json=req.filter_json,
        instructions_override=req.instructions_override,
        attachments=req.attachments,
        scheduled_start_at=req.scheduled_start_at,
    )
    # Delegate to the main campaigns endpoint
    return await create_and_generate(inner, _auth=auth, org_id=org_id)


@router.get("/templates")
async def coo_list_templates(
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    rows = template_domain.list_templates()
    slim = [
        {"name": r["name"], "channel": r["channel"], "language": r["language"], "partner_category": r["partner_category"]}
        for r in rows
    ]
    return envelope_ok(slim)


@router.get("/segments")
async def coo_list_segments(
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    rows = segment_domain.list_segments()
    slim = [
        {"name": r["name"], "description": r["description"], "cached_count": r["cached_recipient_count"]}
        for r in rows
    ]
    return envelope_ok(slim)


class PreviewSegmentRequest(BaseModel):
    segment_name: str | None = None
    filter_json: dict[str, Any] | None = None


@router.post("/preview-segment")
async def coo_preview_segment(
    req: PreviewSegmentRequest,
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    if req.segment_name:
        seg = segment_domain.load_segment(req.segment_name)
        if not seg:
            raise HTTPException(404, f"Segment not found: {req.segment_name}")
        filter_json = seg["filter_json"]
    elif req.filter_json is not None:
        filter_json = req.filter_json
    else:
        raise HTTPException(400, "segment_name or filter_json required")

    try:
        result = segment_domain.preview_segment(filter_json, limit=5)
        return envelope_ok(result)
    except Exception as e:
        raise HTTPException(400, str(e))
