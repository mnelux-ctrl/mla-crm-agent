"""Unit tests for airtable/segments.py — filter_json → Airtable formula.

Filters target the 5 MLA outreach contact tables. All share the same field
schema, so one formula is enough. Event targeting is denormalized onto each
row via ltsm_2025_status / ltsm_2026_status / ltsm_role — no joins.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from airtable.segments import (
    filter_to_airtable_formula,
    validate_filter,
    FilterValidationError,
)


# ── validation ──────────────────────────────────────────────────────────────

def test_unknown_key_rejected():
    with pytest.raises(FilterValidationError):
        validate_filter({"some_random_key": "x"})


def test_empty_dict_is_valid_for_validation():
    validate_filter({})  # no raise


def test_ltsm_role_must_be_list():
    with pytest.raises(FilterValidationError):
        validate_filter({"ltsm_role": "Exhibitor"})
    with pytest.raises(FilterValidationError):
        validate_filter({"ltsm_role": [1, 2]})


def test_empty_list_rejected():
    with pytest.raises(FilterValidationError):
        validate_filter({"country": []})


def test_language_enum_validated():
    validate_filter({"language": "English"})
    validate_filter({"language": "Montenegrin"})
    validate_filter({"language": "any"})
    with pytest.raises(FilterValidationError):
        validate_filter({"language": "Klingon"})


def test_gender_enum_validated():
    validate_filter({"gender": "Male"})
    validate_filter({"gender": "Female"})
    with pytest.raises(FilterValidationError):
        validate_filter({"gender": "M"})


def test_vip_flag_must_be_bool():
    validate_filter({"vip_flag": True})
    with pytest.raises(FilterValidationError):
        validate_filter({"vip_flag": "yes"})


def test_exclude_do_not_contact_bool():
    validate_filter({"exclude_do_not_contact": True})
    with pytest.raises(FilterValidationError):
        validate_filter({"exclude_do_not_contact": "no"})


# ── formula building ────────────────────────────────────────────────────────

def test_empty_filter_safe_default():
    """Empty filter still includes email_primary!='' and NOT(do_not_contact)."""
    formula = filter_to_airtable_formula({})
    assert "{email_primary}!=''" in formula
    assert "{do_not_contact}" in formula


def test_single_ltsm_role():
    formula = filter_to_airtable_formula({"ltsm_role": ["Exhibitor"]})
    assert "{ltsm_role}='Exhibitor'" in formula


def test_multi_ltsm_role_uses_or():
    formula = filter_to_airtable_formula({
        "ltsm_role": ["Exhibitor", "Hosted Buyer"],
        "exclude_do_not_contact": False,
        "require_email": False,
    })
    assert "OR(" in formula
    assert "{ltsm_role}='Exhibitor'" in formula
    assert "{ltsm_role}='Hosted Buyer'" in formula


def test_exhibitors_ltsm_2026_canonical():
    """Stefan's canonical query: all LTSM 2026 exhibitors."""
    formula = filter_to_airtable_formula({"ltsm_2026_status": ["Exhibitor"]})
    assert "{ltsm_2026_status}='Exhibitor'" in formula


def test_buyers_ltsm_2025():
    formula = filter_to_airtable_formula({
        "ltsm_2025_status": ["Confirmed"],
        "ltsm_role": ["Hosted Buyer"],
    })
    assert "{ltsm_2025_status}='Confirmed'" in formula
    assert "{ltsm_role}='Hosted Buyer'" in formula


def test_hotels_in_montenegro():
    """Hotels = company_type ending in Hotel-ish. Country in CG."""
    formula = filter_to_airtable_formula({
        "company_type": ["Hotel"],
        "country": ["Montenegro", "MNE"],
    })
    assert formula.startswith("AND(")
    assert "{company_type}='Hotel'" in formula
    assert "OR({country}='Montenegro', {country}='MNE')" in formula


def test_language_any_skips_clause():
    formula = filter_to_airtable_formula({"language": "any", "ltsm_role": ["Exhibitor"]})
    assert "{language}" not in formula


def test_language_english_included():
    formula = filter_to_airtable_formula({"language": "English", "ltsm_role": ["Exhibitor"]})
    assert "{language}='English'" in formula


def test_gender_filter_included():
    formula = filter_to_airtable_formula({"gender": "Female", "ltsm_role": ["Media"]})
    assert "{gender}='Female'" in formula


def test_vip_flag_true_includes_checkbox_check():
    formula = filter_to_airtable_formula({"vip_flag": True})
    assert "{vip_flag}" in formula
    # Bare checkbox reference, not NOT()
    assert "NOT({vip_flag})" not in formula


def test_vip_flag_false_negates():
    formula = filter_to_airtable_formula({"vip_flag": False})
    assert "NOT({vip_flag})" in formula


# ── safety: formula injection ───────────────────────────────────────────────

def test_single_quote_escaped():
    formula = filter_to_airtable_formula({"country": ["MNE'); DROP"]})
    assert "''" in formula
    assert "MNE'); DROP" not in formula


def test_unknown_key_rejected_by_builder():
    with pytest.raises(FilterValidationError):
        filter_to_airtable_formula({"ssn": ["123"]})
