"""/api/crm/templates/* — manage CRM_TEMPLATE records."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import verify_crm_api_key, get_org_id, envelope_ok
from domain import template as template_domain

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/crm/templates", tags=["templates"])


class TemplateIn(BaseModel):
    name: str
    body_template: str
    subject_template: str = ""
    channel: str = "email"
    language: str = "auto"
    partner_category: str = ""
    attachment_urls: list[str] = Field(default_factory=list)


@router.post("")
async def create(
    req: TemplateIn,
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    try:
        out = template_domain.save_template(req.model_dump())
        return envelope_ok(out)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("create template failed")
        raise HTTPException(500, str(e))


@router.get("")
async def list_all(
    channel: str | None = None,
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    return envelope_ok(template_domain.list_templates(channel=channel))


@router.get("/{name}")
async def get_one(
    name: str,
    _auth: str = Depends(verify_crm_api_key),
    org_id: str = Depends(get_org_id),
):
    out = template_domain.load_template(name)
    if not out:
        raise HTTPException(404, f"Template not found: {name}")
    return envelope_ok(out)
