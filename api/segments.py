"""/api/crm/segments/* — save, list, preview filters."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Any

from airtable.segments import FilterValidationError
from api.deps import verify_crm_api_key, get_org_id, envelope_ok, envelope_err
from domain import segment as segment_domain

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/crm/segments", tags=["segments"])


class PreviewRequest(BaseModel):
    filter_json: dict[str, Any]
    limit: int = Field(default=5, ge=1, le=25)


class SaveRequest(BaseModel):
    name: str
    description: str = ""
    filter_json: dict[str, Any]
    source_table: str = "PERSON"


@router.post("/preview")
async def preview(
    req: PreviewRequest,
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    try:
        result = segment_domain.preview_segment(req.filter_json, limit=req.limit)
        return envelope_ok(result)
    except FilterValidationError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("preview failed")
        raise HTTPException(500, str(e))


@router.post("")
async def save(
    req: SaveRequest,
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    try:
        out = segment_domain.save_segment(req.model_dump())
        return envelope_ok(out)
    except (ValueError, FilterValidationError) as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("save segment failed")
        raise HTTPException(500, str(e))


@router.get("")
async def list_all(
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    return envelope_ok(segment_domain.list_segments())
