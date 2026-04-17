"""
airtable/segments.py — Translate a validated filter_json into an Airtable formula.

Safe allow-list only. Any unknown key is rejected (not silently dropped).
All string values are escaped against formula injection via single-quote doubling.

MLA reality: event targeting is already denormalized onto each contact row as
`ltsm_2025_status`, `ltsm_2026_status`, `ltsm_role`. So a single PERSON-scope
formula is enough — no EVENT_PARTICIPATION join needed. The CRM simply iterates
through the 5 contact tables with this formula.

Example:
  filter_json = {
    "ltsm_2026_status": ["Exhibitor"],
    "country": ["Montenegro", "MNE"],
    "exclude_do_not_contact": True
  }
  →
  AND(
    OR({ltsm_2026_status}='Exhibitor'),
    OR({country}='Montenegro', {country}='MNE'),
    NOT({do_not_contact}),
    {email_primary}!=''
  )
"""

from __future__ import annotations

from typing import Any


# Allowed keys → Airtable field name. Maps directly to ContactField constants.
ALLOWED_LIST_FIELDS: dict[str, str] = {
    # Where are they / what are they
    "country":          "country",
    "company_type":     "company_type",
    "partner_type":     "partner_type",
    "ltsm_role":        "ltsm_role",
    "ltsm_2025_status": "ltsm_2025_status",
    "ltsm_2026_status": "ltsm_2026_status",
    "relationship_stage": "relationship_stage",
    "priority":         "priority",
    "role_title":       "role_title",     # free-text job title
    "company":          "company",        # company name (free-text across all tables)
    "city":             "city",
}

ALLOWED_ENUM_FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "language": ("language", ("English", "Montenegrin", "Serbian", "any")),
    "gender":   ("gender",   ("Male", "Female", "any")),
}

# Boolean filters (checkbox field direct value).
ALLOWED_BOOL_FIELDS: dict[str, str] = {
    "vip_flag":       "vip_flag",
    "awaiting_reply": "awaiting_reply",
}


class FilterValidationError(ValueError):
    """Raised when filter_json contains unknown/invalid keys."""


def _escape(value: str) -> str:
    """Airtable formula string escaping: double the single quotes."""
    return value.replace("'", "''")


def _list_clause(field: str, values: list[str]) -> str:
    parts = [f"{{{field}}}='{_escape(v)}'" for v in values]
    if len(parts) == 1:
        return parts[0]
    return "OR(" + ", ".join(parts) + ")"


def validate_filter(filter_json: dict[str, Any]) -> None:
    """Raise FilterValidationError if anything unknown/malformed is present."""
    if not isinstance(filter_json, dict):
        raise FilterValidationError("filter_json must be an object")

    allowed_keys = (
        set(ALLOWED_LIST_FIELDS)
        | set(ALLOWED_ENUM_FIELDS)
        | set(ALLOWED_BOOL_FIELDS)
        | {"exclude_do_not_contact", "require_email"}
    )
    unknown = set(filter_json) - allowed_keys
    if unknown:
        raise FilterValidationError(
            f"Unknown filter keys: {sorted(unknown)}. "
            f"Allowed: {sorted(allowed_keys)}"
        )

    for key in ALLOWED_LIST_FIELDS:
        if key in filter_json:
            val = filter_json[key]
            if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
                raise FilterValidationError(
                    f"Filter key {key!r} must be a list of strings"
                )
            if not val:
                raise FilterValidationError(
                    f"Filter key {key!r} is empty — omit the key instead"
                )

    for key, (_, allowed_values) in ALLOWED_ENUM_FIELDS.items():
        if key in filter_json:
            val = filter_json[key]
            if not isinstance(val, str) or val not in allowed_values:
                raise FilterValidationError(
                    f"Filter key {key!r} must be one of {allowed_values}"
                )

    for key in ALLOWED_BOOL_FIELDS:
        if key in filter_json and not isinstance(filter_json[key], bool):
            raise FilterValidationError(f"Filter key {key!r} must be boolean")

    for bkey in ("exclude_do_not_contact", "require_email"):
        if bkey in filter_json and not isinstance(filter_json[bkey], bool):
            raise FilterValidationError(f"Filter key {bkey!r} must be boolean")


def filter_to_airtable_formula(filter_json: dict[str, Any]) -> str:
    """Build the final AND(...) formula string. Validates first.

    Applies to any of the 5 contact tables (they share the same field schema).
    """
    validate_filter(filter_json)

    clauses: list[str] = []

    for key, field in ALLOWED_LIST_FIELDS.items():
        if key in filter_json:
            clauses.append(_list_clause(field, filter_json[key]))

    if "language" in filter_json and filter_json["language"] != "any":
        clauses.append(f"{{language}}='{_escape(filter_json['language'])}'")

    if "gender" in filter_json and filter_json["gender"] != "any":
        clauses.append(f"{{gender}}='{_escape(filter_json['gender'])}'")

    for key, field in ALLOWED_BOOL_FIELDS.items():
        if key in filter_json:
            if filter_json[key] is True:
                clauses.append(f"{{{field}}}")
            else:
                clauses.append(f"NOT({{{field}}})")

    # Defaults: exclude opt-outs, require email
    if filter_json.get("exclude_do_not_contact", True):
        clauses.append("NOT({do_not_contact})")
    if filter_json.get("require_email", True):
        clauses.append("{email_primary}!=''")

    if not clauses:
        clauses.append("{email_primary}!=''")

    if len(clauses) == 1:
        return clauses[0]
    return "AND(" + ", ".join(clauses) + ")"
