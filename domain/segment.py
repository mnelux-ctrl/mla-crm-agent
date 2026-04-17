"""
domain/segment.py — CRM_SEGMENT loader/saver + preview.

Segments are saved filters. Stefan can name them ("Hotels MNE Sales+Marketing")
in Airtable and reuse them from voice commands. Queries run across all 5
outreach contact tables and return the union.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from airtable import client as airtable_client
from airtable.schema import SegmentField, ContactField
from airtable.segments import filter_to_airtable_formula, validate_filter
import config

logger = logging.getLogger(__name__)


def load_segment(name: str) -> dict[str, Any] | None:
    rec = airtable_client.get_segment_by_name(name)
    if not rec:
        return None
    return _parse(rec)


def list_segments() -> list[dict[str, Any]]:
    return [_parse(r) for r in airtable_client.list_segments()]


def save_segment(data: dict[str, Any]) -> dict[str, Any]:
    if not data.get("name"):
        raise ValueError("segment.name is required")
    if not isinstance(data.get("filter_json"), dict):
        raise ValueError("segment.filter_json must be a dict")
    validate_filter(data["filter_json"])

    fields = {
        SegmentField.NAME: data["name"],
        SegmentField.DESCRIPTION: data.get("description", ""),
        SegmentField.FILTER_JSON: json.dumps(data["filter_json"]),
        SegmentField.SOURCE_TABLE: data.get("source_table", "ALL"),
        SegmentField.IS_ARCHIVED: False,
    }
    rec = airtable_client.create_segment(fields)
    return _parse(rec)


def _source_tables_for(filter_json: dict[str, Any], segment_override: str | None = None) -> list[str]:
    """Pick which contact tables to search based on segment.source_table or keep default (all 5)."""
    if segment_override and segment_override != "ALL":
        if segment_override in config.AIRTABLE_CONTACT_TABLES:
            return [segment_override]
    return config.AIRTABLE_CONTACT_TABLES


def preview_segment(
    filter_json: dict[str, Any],
    *,
    limit: int = 5,
    source_table: str | None = None,
) -> dict[str, Any]:
    """Run the filter and return count + example recipients (no side effects)."""
    validate_filter(filter_json)
    formula = filter_to_airtable_formula(filter_json)

    tables = _source_tables_for(filter_json, source_table)
    rows = airtable_client.fetch_contacts_by_formula(formula, table_names=tables)

    examples = []
    for r in rows[:limit]:
        f = r.get("fields", {})
        examples.append({
            "id": r.get("id"),
            "table": r.get("_source_table"),
            "first_name": f.get(ContactField.FIRST_NAME, ""),
            "last_name": f.get(ContactField.LAST_NAME, ""),
            "email": f.get(ContactField.EMAIL_PRIMARY, ""),
            "company": f.get(ContactField.COMPANY, ""),
            "ltsm_role": f.get(ContactField.LTSM_ROLE, ""),
            "ltsm_2026_status": f.get(ContactField.LTSM_2026_STATUS, ""),
            "country": f.get(ContactField.COUNTRY, ""),
        })
    return {
        "count": len(rows),
        "examples": examples,
        "formula": formula,
        "tables_searched": tables,
    }


def recipients_for_filter(
    filter_json: dict[str, Any],
    *,
    source_table: str | None = None,
) -> list[dict[str, Any]]:
    """Return full flattened recipient list ready for campaign drafting."""
    validate_filter(filter_json)
    formula = filter_to_airtable_formula(filter_json)

    tables = _source_tables_for(filter_json, source_table)
    rows = airtable_client.fetch_contacts_by_formula(formula, table_names=tables)

    out = []
    for r in rows:
        f = r.get("fields", {})
        email = (f.get(ContactField.EMAIL_PRIMARY) or "").strip()
        if not email:
            continue
        out.append({
            "airtable_id": r.get("id"),
            "source_table": r.get("_source_table", ""),
            "email": email,
            "first_name": f.get(ContactField.FIRST_NAME, ""),
            "last_name": f.get(ContactField.LAST_NAME, ""),
            "salutation": f.get(ContactField.SALUTATION, ""),
            "language": f.get(ContactField.LANGUAGE, ""),
            "gender": f.get(ContactField.GENDER, ""),
            "country": f.get(ContactField.COUNTRY, ""),
            "company_name": f.get(ContactField.COMPANY, ""),
            "role_title": f.get(ContactField.ROLE_TITLE, ""),
            "ltsm_role": f.get(ContactField.LTSM_ROLE, ""),
            "ltsm_2025_status": f.get(ContactField.LTSM_2025_STATUS, ""),
            "ltsm_2026_status": f.get(ContactField.LTSM_2026_STATUS, ""),
            "vip_flag": bool(f.get(ContactField.VIP_FLAG, False)),
        })
    return out


def _parse(rec: dict) -> dict[str, Any]:
    f = rec.get("fields", {})
    filter_raw = f.get(SegmentField.FILTER_JSON, "{}")
    try:
        filter_json = json.loads(filter_raw) if filter_raw else {}
    except (ValueError, TypeError):
        filter_json = {}
    return {
        "airtable_id": rec.get("id", ""),
        "name": f.get(SegmentField.NAME, ""),
        "description": f.get(SegmentField.DESCRIPTION, ""),
        "filter_json": filter_json,
        "source_table": f.get(SegmentField.SOURCE_TABLE, "ALL"),
        "cached_recipient_count": f.get(SegmentField.CACHED_RECIPIENT_COUNT, 0),
        "cached_at": f.get(SegmentField.CACHED_AT, ""),
    }
