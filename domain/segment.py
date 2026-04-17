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


def _excluded_person_ids_from_campaigns(campaign_ids: list[str]) -> set[str]:
    """Query EMAIL_LOG for all PERSON IDs already emailed in the given campaigns.

    Returns set of Airtable record IDs to exclude from the current filter.
    Non-fatal on any Airtable error (returns empty set).
    """
    if not campaign_ids:
        return set()
    try:
        from airtable import client as ac
        from airtable.schema import EmailLogField
        clauses = [f"{{{EmailLogField.CAMPAIGN_ID}}}='{cid}'" for cid in campaign_ids if cid]
        if not clauses:
            return set()
        formula = f"OR({', '.join(clauses)})" if len(clauses) > 1 else clauses[0]
        import config as cfg
        api = ac._get_api()
        table = api.table(cfg.AIRTABLE_BASE_ID, cfg.AIRTABLE_EMAIL_LOG_TABLE)
        rows = table.all(formula=formula, max_records=10000)
        excluded: set[str] = set()
        for r in rows:
            link = r.get("fields", {}).get(EmailLogField.CONTACT_LINK, [])
            if isinstance(link, list):
                excluded.update(link)
        return excluded
    except Exception as e:
        logger.warning(f"_excluded_person_ids_from_campaigns soft-failed: {e}")
        return set()


def _resolve_auto_cc(
    primary_recipients: list[dict],
    cc_role_title: list[str] | None,
    cc_ltsm_role: list[str] | None,
) -> None:
    """Fill each recipient's `cc` list with same-company colleagues matching the
    auto-CC filter. Mutates primary_recipients in place.

    Example: primary filter matches sales directors of each hotel. auto-CC
    filter matches PR + marketing managers. For each sales director, CRM
    queries all contact tables for same-company colleagues with role_title in
    ["PR Manager", "Marketing"]; those emails go into cc.

    Colleagues that would duplicate the primary (same email, or already in the
    primary recipient list for this campaign) are excluded.
    """
    cc_role_title = cc_role_title or []
    cc_ltsm_role = cc_ltsm_role or []
    if not cc_role_title and not cc_ltsm_role:
        return

    # Build filter formula for CC candidates
    clauses = []
    if cc_role_title:
        parts = [f"{{role_title}}='{r.replace(chr(39), chr(39)*2)}'" for r in cc_role_title]
        clauses.append("OR(" + ", ".join(parts) + ")" if len(parts) > 1 else parts[0])
    if cc_ltsm_role:
        parts = [f"{{ltsm_role}}='{r.replace(chr(39), chr(39)*2)}'" for r in cc_ltsm_role]
        clauses.append("OR(" + ", ".join(parts) + ")" if len(parts) > 1 else parts[0])
    clauses.append("NOT({do_not_contact})")
    clauses.append("{email_primary}!=''")
    formula = "AND(" + ", ".join(clauses) + ")"

    # Fetch all possible CC contacts once (cheap — usually 10-50)
    cc_pool = airtable_client.fetch_contacts_by_formula(formula)

    # Group by company (case-insensitive, trimmed)
    def normkey(c: str) -> str:
        return (c or "").strip().lower()

    by_company: dict[str, list[dict]] = {}
    for r in cc_pool:
        f = r.get("fields", {})
        by_company.setdefault(normkey(f.get("company", "")), []).append(r)

    # Track which emails are already primary recipients — don't double-include
    primary_emails = {r["email"].lower() for r in primary_recipients}

    for primary in primary_recipients:
        company = normkey(primary.get("company_name", ""))
        colleagues = by_company.get(company, [])
        cc = []
        for col in colleagues:
            cf = col.get("fields", {})
            col_email = (cf.get("email_primary") or "").strip().lower()
            if not col_email:
                continue
            if col_email == primary["email"].lower():
                continue  # skip self
            if col_email in primary_emails:
                continue  # they'll get their own email as a primary
            if col_email in cc:
                continue  # dedup
            cc.append(col_email)
        primary["cc"] = cc


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

    # Anti-duplication: filter out contacts already in specified campaigns
    exclude_campaigns = filter_json.get("exclude_in_campaign") or []
    if exclude_campaigns:
        excluded_ids = _excluded_person_ids_from_campaigns(exclude_campaigns)
        if excluded_ids:
            before = len(rows)
            rows = [r for r in rows if r.get("id") not in excluded_ids]
            logger.info(f"exclude_in_campaign: removed {before - len(rows)} recipients")

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
            "cc": [],  # filled below if cc_role_title / cc_ltsm_role provided
        })

    # Auto-CC: add same-company colleagues to each primary recipient's cc list
    _resolve_auto_cc(
        out,
        cc_role_title=filter_json.get("cc_role_title"),
        cc_ltsm_role=filter_json.get("cc_ltsm_role"),
    )
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
