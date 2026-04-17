"""
api/deps.py — Common FastAPI dependencies (auth, org_id).
"""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

import config


import hmac


def _ct_compare(a: str, b: str) -> bool:
    """Constant-time string compare to prevent timing attacks on token auth."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def verify_crm_api_key(authorization: str = Header(...)) -> str:
    """Public-ish endpoint auth (COO and frontend)."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization[7:].strip()
    if not _ct_compare(token, config.CRM_API_KEY):
        raise HTTPException(403, "Invalid CRM_API_KEY")
    return token


def verify_crm_internal_key(authorization: str = Header(...)) -> str:
    """Service-to-service auth (email-agent callbacks)."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization[7:].strip()
    if not _ct_compare(token, config.CRM_INTERNAL_KEY):
        raise HTTPException(403, "Invalid CRM_INTERNAL_KEY")
    return token


def get_org_id(x_org_id: str | None = Header(default=None)) -> str:
    return x_org_id or config.DEFAULT_ORG_ID


def envelope_ok(data: dict | list | str | None = None) -> dict:
    return {"ok": True, "data": data if data is not None else {}}


def envelope_err(message: str, code: str = "error") -> dict:
    return {"ok": False, "error": message, "error_code": code}
