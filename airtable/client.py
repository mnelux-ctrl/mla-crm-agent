"""
airtable/client.py — Thin wrapper around pyairtable for CRM tables.

MLA's contact data lives across 5 tables (National Companies, National Producers,
National Institutions, International Buyers, International Partners). All share
the same field schema. fetch_contacts_by_formula() runs the same formula
against each table and returns the union, annotated with the source table name.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pyairtable import Api

import config
from airtable.schema import (
    CampaignField,
    SegmentField,
    TemplateField,
    EmailLogField,
    ContactField,
)

logger = logging.getLogger(__name__)

_api: Api | None = None


def _get_api() -> Api:
    global _api
    if _api is None:
        _api = Api(config.AIRTABLE_PAT)
    return _api


def _table(table_name: str):
    return _get_api().table(config.AIRTABLE_BASE_ID, table_name)


# ── CONTACTS: multi-table union ─────────────────────────────────────────────

def fetch_contacts_by_formula(
    formula: str,
    *,
    table_names: list[str] | None = None,
    max_records_per_table: int = 10000,
) -> list[dict]:
    """Run `formula` against each contact table and return annotated union.

    Each returned record has an extra `_source_table` key so downstream code
    can update the right table when writing last_outbound_thread_id etc.
    """
    tables = table_names if table_names is not None else config.AIRTABLE_CONTACT_TABLES
    out: list[dict] = []
    for tname in tables:
        try:
            rows = _table(tname).all(formula=formula, max_records=max_records_per_table)
            for r in rows:
                r["_source_table"] = tname
                out.append(r)
        except Exception as e:
            # One table failing (e.g. formula references a field that doesn't
            # exist on that table) shouldn't kill the whole query. Log + skip.
            logger.warning(f"fetch_contacts_by_formula: table {tname!r} failed: {e}")
    return out


def fetch_contact_by_email(email: str) -> tuple[dict | None, str | None]:
    """Find a single contact by email across all tables. Returns (record, table_name)."""
    if not email:
        return None, None
    escaped = email.replace("'", "''")
    formula = f"OR({{email_primary}}='{escaped}', {{email_secondary}}='{escaped}')"
    for tname in config.AIRTABLE_CONTACT_TABLES:
        try:
            rows = _table(tname).all(formula=formula, max_records=1)
            if rows:
                return rows[0], tname
        except Exception as e:
            logger.warning(f"fetch_contact_by_email: {tname!r} failed: {e}")
    return None, None


def update_contact_last_outbound(
    table_name: str,
    record_id: str,
    gmail_thread_id: str,
    sent_at: datetime,
) -> None:
    """Write last_outbound_* on whichever contact table this row belongs to."""
    try:
        _table(table_name).update(record_id, {
            ContactField.LAST_OUTBOUND_THREAD_ID: gmail_thread_id,
            ContactField.LAST_OUTBOUND_AT: sent_at.isoformat(),
        })
    except Exception as e:
        logger.warning(f"update_contact_last_outbound({table_name}/{record_id}) soft-failed: {e}")


# Legacy aliases — keep for backward compatibility during the refactor.
fetch_persons_by_formula = fetch_contacts_by_formula
fetch_person_by_email = fetch_contact_by_email


def update_person_last_outbound(person_id, gmail_thread_id, sent_at, table_name=None):
    """Legacy shim. New callers should use update_contact_last_outbound with table_name."""
    if table_name is None:
        logger.warning("update_person_last_outbound called without table_name — skipping")
        return
    return update_contact_last_outbound(table_name, person_id, gmail_thread_id, sent_at)


# ── CRM_TEMPLATE ────────────────────────────────────────────────────────────

def get_template_by_name(name: str) -> dict | None:
    escaped = name.replace("'", "''")
    formula = f"AND({{{TemplateField.NAME}}}='{escaped}', NOT({{{TemplateField.IS_ARCHIVED}}}))"
    rows = _table(config.AIRTABLE_CRM_TEMPLATE_TABLE).all(formula=formula, max_records=1)
    return rows[0] if rows else None


def list_templates(channel: str | None = None) -> list[dict]:
    clauses = [f"NOT({{{TemplateField.IS_ARCHIVED}}})"]
    if channel:
        escaped = channel.replace("'", "''")
        clauses.append(f"{{{TemplateField.CHANNEL}}}='{escaped}'")
    formula = "AND(" + ", ".join(clauses) + ")"
    return _table(config.AIRTABLE_CRM_TEMPLATE_TABLE).all(formula=formula)


def create_template(fields: dict) -> dict:
    fields.setdefault(TemplateField.ORG_ID, config.DEFAULT_ORG_ID)
    return _table(config.AIRTABLE_CRM_TEMPLATE_TABLE).create(fields)


# ── CRM_SEGMENT ─────────────────────────────────────────────────────────────

def get_segment_by_name(name: str) -> dict | None:
    escaped = name.replace("'", "''")
    formula = f"AND({{{SegmentField.NAME}}}='{escaped}', NOT({{{SegmentField.IS_ARCHIVED}}}))"
    rows = _table(config.AIRTABLE_CRM_SEGMENT_TABLE).all(formula=formula, max_records=1)
    return rows[0] if rows else None


def list_segments() -> list[dict]:
    formula = f"NOT({{{SegmentField.IS_ARCHIVED}}})"
    return _table(config.AIRTABLE_CRM_SEGMENT_TABLE).all(formula=formula)


def create_segment(fields: dict) -> dict:
    fields.setdefault(SegmentField.ORG_ID, config.DEFAULT_ORG_ID)
    return _table(config.AIRTABLE_CRM_SEGMENT_TABLE).create(fields)


def update_segment_cache(segment_record_id: str, count: int) -> None:
    try:
        _table(config.AIRTABLE_CRM_SEGMENT_TABLE).update(segment_record_id, {
            SegmentField.CACHED_RECIPIENT_COUNT: count,
            SegmentField.CACHED_AT: datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.warning(f"update_segment_cache soft-failed: {e}")


# ── CRM_CAMPAIGN ────────────────────────────────────────────────────────────

def create_campaign(fields: dict) -> dict:
    fields.setdefault(CampaignField.ORG_ID, config.DEFAULT_ORG_ID)
    fields.setdefault(CampaignField.CREATED_AT, datetime.now(timezone.utc).isoformat())
    return _table(config.AIRTABLE_CRM_CAMPAIGN_TABLE).create(fields)


def get_campaign_by_id(campaign_id: str) -> dict | None:
    escaped = campaign_id.replace("'", "''")
    formula = f"{{{CampaignField.CAMPAIGN_ID}}}='{escaped}'"
    rows = _table(config.AIRTABLE_CRM_CAMPAIGN_TABLE).all(formula=formula, max_records=1)
    return rows[0] if rows else None


def update_campaign(record_id: str, fields: dict) -> dict:
    return _table(config.AIRTABLE_CRM_CAMPAIGN_TABLE).update(record_id, fields)


# ── EMAIL_LOG ──────────────────────────────────────────────────────────────

def log_outbound_email(
    *,
    campaign_id: str,
    recipient_person_id: str | None,
    recipient_email: str,
    subject: str,
    gmail_message_id: str,
    gmail_thread_id: str,
    sent_at: datetime,
    scheduled_send_at: datetime | None = None,
) -> dict:
    fields: dict[str, Any] = {
        EmailLogField.EMAIL_SUBJECT: subject,
        EmailLogField.LOG_DATE: sent_at.isoformat(),
        EmailLogField.DIRECTION: "Outbound",
        EmailLogField.EMAIL_FROM: "stefan@mnelux.com",
        EmailLogField.SEND_AS: "stefan@mnelux.com",
        EmailLogField.EMAIL_TO: recipient_email,
        EmailLogField.EMAIL_TYPE: "crm_campaign",
        EmailLogField.CLASSIFICATION: "Sent",
        EmailLogField.DECISION: "Approved",
        EmailLogField.CAMPAIGN_ID: campaign_id,
        EmailLogField.GMAIL_MESSAGE_ID: gmail_message_id,
        EmailLogField.GMAIL_THREAD_ID: gmail_thread_id,
    }
    if recipient_person_id:
        fields[EmailLogField.CONTACT_LINK] = [recipient_person_id]
    if scheduled_send_at:
        fields[EmailLogField.SCHEDULED_SEND_AT] = scheduled_send_at.isoformat()
    try:
        return _table(config.AIRTABLE_EMAIL_LOG_TABLE).create(fields)
    except Exception as e:
        logger.warning(f"log_outbound_email failed: {e}")
        return {}


def count_sent_today() -> int:
    """Count outbound emails sent in the last 24h."""
    formula = (
        "AND("
        f"{{{EmailLogField.DIRECTION}}}='Outbound', "
        f"DATETIME_DIFF(NOW(), {{{EmailLogField.LOG_DATE}}}, 'hours') <= 24"
        ")"
    )
    try:
        rows = _table(config.AIRTABLE_EMAIL_LOG_TABLE).all(formula=formula)
        return len(rows)
    except Exception as e:
        logger.warning(f"count_sent_today failed: {e}")
        return 0


def log_send_error(
    *,
    campaign_id: str,
    recipient_person_id: str | None,
    recipient_email: str,
    subject: str,
    error: str,
) -> dict | None:
    fields: dict[str, Any] = {
        EmailLogField.EMAIL_SUBJECT: subject,
        EmailLogField.DIRECTION: "Outbound",
        EmailLogField.EMAIL_TO: recipient_email,
        EmailLogField.EMAIL_TYPE: "crm_campaign",
        EmailLogField.CLASSIFICATION: "Failed",
        EmailLogField.DECISION: "Approved",
        EmailLogField.CAMPAIGN_ID: campaign_id,
        EmailLogField.SEND_ERROR: error[:1000],
    }
    if recipient_person_id:
        fields[EmailLogField.CONTACT_LINK] = [recipient_person_id]
    try:
        return _table(config.AIRTABLE_EMAIL_LOG_TABLE).create(fields)
    except Exception as e:
        logger.error(f"log_send_error failed: {e}")
        return None
