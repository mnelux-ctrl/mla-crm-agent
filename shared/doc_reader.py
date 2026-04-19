"""
shared/doc_reader.py — thin HTTP client for the MLA Doc Reader service.

Upstream source: https://github.com/mnelux-ctrl/mla-doc-reader
                 (shared_client/doc_reader.py)

This file is a VERBATIM copy. Do not edit — pull updates from upstream.

Env vars required by this module:
    DOC_READER_URL       https://mla-doc-reader-production.up.railway.app
    DOC_READER_API_KEY   shared secret (same value on every MLA service)
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

import httpx

log = logging.getLogger(__name__)

_DEFAULT_URL = "https://mla-doc-reader-production.up.railway.app"


def _endpoint(path: str) -> str:
    base = os.environ.get("DOC_READER_URL", _DEFAULT_URL).rstrip("/")
    return f"{base}{path}"


def _headers() -> dict:
    key = os.environ.get("DOC_READER_API_KEY", "")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def read_document_url(
    url: str,
    *,
    slack_bot_token: Optional[str] = None,
    force_refresh: bool = False,
    timeout: float = 60.0,
) -> dict:
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.post(
                _endpoint("/api/docs/read-url"),
                headers=_headers(),
                json={
                    "url": url,
                    "slack_bot_token": slack_bot_token,
                    "force_refresh": force_refresh,
                },
            )
    except httpx.HTTPError as e:
        log.warning(f"doc-reader unreachable: {e}")
        return {"ok": False, "error": f"doc-reader unreachable: {e}"}
    if r.status_code != 200:
        return {"ok": False, "error": f"doc-reader returned {r.status_code}: {r.text[:300]}"}
    try:
        return r.json()
    except Exception as e:
        return {"ok": False, "error": f"doc-reader returned non-JSON: {e}"}


def read_bytes(
    data: bytes,
    *,
    filename: str = "",
    content_type: Optional[str] = None,
    timeout: float = 60.0,
) -> dict:
    b64 = base64.b64encode(data or b"").decode("ascii")
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.post(
                _endpoint("/api/docs/read-bytes"),
                headers=_headers(),
                json={
                    "filename": filename,
                    "content_type": content_type,
                    "data_b64": b64,
                },
            )
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"doc-reader unreachable: {e}"}
    if r.status_code != 200:
        return {"ok": False, "error": f"doc-reader returned {r.status_code}: {r.text[:300]}"}
    try:
        return r.json()
    except Exception as e:
        return {"ok": False, "error": f"doc-reader returned non-JSON: {e}"}


def read_slack_file(slack_file_obj: dict, slack_bot_token: str) -> dict:
    download_url = (
        slack_file_obj.get("url_private_download")
        or slack_file_obj.get("url_private")
        or ""
    )
    if not download_url:
        return {"ok": False, "error": "Slack file has no url_private_download"}
    return read_document_url(download_url, slack_bot_token=slack_bot_token)


CLAUDE_TOOL_SCHEMA = {
    "name": "read_document",
    "description": (
        "Read and extract plain text from ANY document Stefan (or someone else) has "
        "shared — a Google Doc/Sheet/Slide URL, a Google Drive file URL, a Slack file "
        "attachment URL, or an arbitrary public HTTPS URL. Use this whenever there is "
        "a document URL mentioned in the conversation or a file has been attached to "
        "a Slack message. Supports PDF, DOCX, XLSX, PPTX, Google Workspace formats, "
        "plain text, Markdown, CSV, JSON, HTML."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": (
                    "URL of the document. Accepts Google Docs/Sheets/Slides URLs, "
                    "Google Drive file URLs, Slack file URLs, and public HTTPS URLs."
                ),
            },
        },
        "required": ["url"],
    },
}

OPENAI_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_document",
        "description": CLAUDE_TOOL_SCHEMA["description"],
        "parameters": CLAUDE_TOOL_SCHEMA["input_schema"],
    },
}
