"""/api/crm/sequences/* — direct HTTP control of sequences.

Complements brain/tools.py (which covers the same via GPT). Raw HTTP is useful
for automation, the future frontend, and deterministic testing.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import verify_crm_api_key, get_org_id, envelope_ok
from airtable.segments import FilterValidationError
from domain import sequence as seq_mod
from domain import segment as segment_domain

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/crm/sequences", tags=["sequences"])


class EnrollRequest(BaseModel):
    segment_name: str | None = None
    filter_json: dict[str, Any] | None = None
    skip_already_enrolled: bool = True


@router.get("")
async def list_sequences(
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    return envelope_ok(seq_mod.list_sequences())


@router.get("/{sequence_name}/overview")
async def overview(
    sequence_name: str,
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    return envelope_ok(seq_mod.sequence_overview(sequence_name))


@router.post("/{sequence_name}/enroll")
async def enroll(
    sequence_name: str,
    req: EnrollRequest,
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    """Enroll all contacts matching the filter/segment into a sequence.

    - Each new contact starts at step 1 (step 1's delay_days after now).
    - Existing contacts already in an active sequence are skipped
      (unless skip_already_enrolled=false).
    """
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
        contacts = segment_domain.recipients_for_filter(filter_json)
    except FilterValidationError as e:
        raise HTTPException(400, str(e))

    if not contacts:
        raise HTTPException(400, "Segment matched 0 recipients")

    result = seq_mod.enroll_in_sequence(
        contacts, sequence_name,
        skip_already_enrolled=req.skip_already_enrolled,
    )
    return envelope_ok(result)


class PauseRequest(BaseModel):
    email: str
    reason: str = "manual"


@router.post("/contacts/pause")
async def pause(
    req: PauseRequest,
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    return envelope_ok(seq_mod.pause_sequence_for_contact(req.email, reason=req.reason))


@router.post("/contacts/resume")
async def resume(
    req: PauseRequest,
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    return envelope_ok(seq_mod.resume_sequence_for_contact(req.email))


@router.post("/contacts/cancel")
async def cancel(
    req: PauseRequest,
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    return envelope_ok(seq_mod.cancel_sequence_for_contact(req.email))


@router.post("/tick")
async def tick(
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    """Manually trigger one sequence tick (what the APScheduler job does every 5min).
    Useful for tests — no wait for next interval."""
    result = await seq_mod.tick_due_steps()
    return envelope_ok(result)
