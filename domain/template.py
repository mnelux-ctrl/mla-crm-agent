"""
domain/template.py — CRM_TEMPLATE loader/saver.

Templates live in Airtable so Stefan can edit them in the browser without deploys.
"""

from __future__ import annotations

import logging
from typing import Any

from airtable import client as airtable_client
from airtable.schema import TemplateField

logger = logging.getLogger(__name__)


def load_template(name: str) -> dict[str, Any] | None:
    """Return flat template dict or None. Shape:
    {
      "name": str, "channel": "email"/"whatsapp"/"linkedin",
      "language": "en"/"mne"/"auto",
      "subject_template": str, "body_template": str,
      "attachment_urls": [str], "partner_category": str
    }
    """
    rec = airtable_client.get_template_by_name(name)
    if not rec:
        return None
    return _parse(rec)


def list_templates(channel: str | None = None) -> list[dict[str, Any]]:
    return [_parse(r) for r in airtable_client.list_templates(channel=channel)]


def save_template(data: dict[str, Any]) -> dict[str, Any]:
    """Create a new template. Required: name, body_template. Optional: others."""
    if not data.get("name"):
        raise ValueError("template.name is required")
    if not data.get("body_template"):
        raise ValueError("template.body_template is required")

    fields: dict[str, Any] = {
        TemplateField.NAME: data["name"],
        TemplateField.BODY_TEMPLATE: data["body_template"],
        TemplateField.CHANNEL: data.get("channel", "email"),
        TemplateField.LANGUAGE: data.get("language", "auto"),
        TemplateField.SUBJECT_TEMPLATE: data.get("subject_template", ""),
        TemplateField.PARTNER_CATEGORY: data.get("partner_category", ""),
        TemplateField.IS_ARCHIVED: False,
    }
    if data.get("attachment_urls"):
        import json
        fields[TemplateField.ATTACHMENT_URLS] = json.dumps(data["attachment_urls"])
    rec = airtable_client.create_template(fields)
    return _parse(rec)


def _parse(rec: dict) -> dict[str, Any]:
    f = rec.get("fields", {})
    import json
    attachment_urls_raw = f.get(TemplateField.ATTACHMENT_URLS, "")
    try:
        attachment_urls = json.loads(attachment_urls_raw) if attachment_urls_raw else []
    except (ValueError, TypeError):
        attachment_urls = []
    return {
        "airtable_id": rec.get("id", ""),
        "name": f.get(TemplateField.NAME, ""),
        "channel": f.get(TemplateField.CHANNEL, "email"),
        "language": f.get(TemplateField.LANGUAGE, "auto"),
        "subject_template": f.get(TemplateField.SUBJECT_TEMPLATE, ""),
        "body_template": f.get(TemplateField.BODY_TEMPLATE, ""),
        "attachment_urls": attachment_urls,
        "partner_category": f.get(TemplateField.PARTNER_CATEGORY, ""),
    }
