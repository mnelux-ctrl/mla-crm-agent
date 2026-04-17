"""Unit tests for personalization/resolver.py."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure package root is on sys.path when running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from personalization.resolver import (
    resolve_language,
    resolve_placeholders,
    build_value_map,
    list_placeholders,
    find_unknown_placeholders,
    find_unresolved_for_recipient,
)


# ── resolve_language ────────────────────────────────────────────────────────

def test_language_from_explicit_mne():
    assert resolve_language({"language": "Montenegrin"}) == "mne"

def test_language_from_srb():
    assert resolve_language({"language": "Serbian"}) == "mne"  # treat SRB as MNE for salutation

def test_language_from_country_montenegro():
    assert resolve_language({"country": "Montenegro"}) == "mne"

def test_language_from_country_crna_gora():
    assert resolve_language({"country": "Crna Gora"}) == "mne"

def test_language_from_english_defaults_english():
    assert resolve_language({"language": "English"}) == "en"

def test_language_empty_defaults_english():
    assert resolve_language({}) == "en"


# ── build_value_map + salutation fallback ───────────────────────────────────

def test_salutation_override_wins():
    person = {"salutation": "Draga Marijana", "first_name": "Marijana", "language": "Montenegrin"}
    vm = build_value_map(person)
    assert vm["salutation"] == "Draga Marijana"

def test_salutation_fallback_mne_with_name():
    person = {"first_name": "Marko", "language": "Montenegrin"}
    vm = build_value_map(person)
    assert vm["salutation"] == "Poštovani Marko"

def test_salutation_fallback_en_with_name():
    person = {"first_name": "Isabel", "language": "English"}
    vm = build_value_map(person)
    assert vm["salutation"] == "Dear Isabel"

def test_salutation_fallback_mne_no_name():
    person = {"country": "Montenegro"}
    vm = build_value_map(person)
    assert vm["salutation"] == "Poštovani"

def test_salutation_fallback_en_no_name():
    person = {}
    vm = build_value_map(person)
    assert vm["salutation"] == "Hello"

def test_full_name_assembled():
    vm = build_value_map({"first_name": "Marko", "last_name": "Petrović"})
    assert vm["full_name"] == "Marko Petrović"

def test_full_name_missing_last_name():
    vm = build_value_map({"first_name": "Marko"})
    assert vm["full_name"] == "Marko"


# ── resolve_placeholders (end-to-end substitution) ──────────────────────────

def test_resolve_all_placeholders():
    body = "{{salutation}}, welcome to {{event_name}} from {{company_name}}. — {{sender_name}}, {{sender_title}}"
    person = {"salutation": "Draga Leonarda", "first_name": "Leonarda", "company_name": "Chedi Luštica Bay"}
    sender = {"name": "Stefan Stešević", "title": "Founder"}
    event = {"name": "LTSM 2026"}
    out = resolve_placeholders(body, person, sender, event)
    assert out == "Draga Leonarda, welcome to LTSM 2026 from Chedi Luštica Bay. — Stefan Stešević, Founder"

def test_unknown_placeholder_kept_literal():
    body = "Hi {{first_name}}, see {{unknown_field}} here."
    out = resolve_placeholders(body, {"first_name": "Marko"})
    assert "{{unknown_field}}" in out
    assert "Marko" in out

def test_missing_value_becomes_empty_string():
    body = "{{first_name}}-{{last_name}}-done"
    out = resolve_placeholders(body, {"first_name": "Marko"})
    assert out == "Marko--done"

def test_salutation_placeholder_always_resolvable():
    body = "{{salutation}}!"
    # Even with empty person, salutation has a fallback
    assert resolve_placeholders(body, {}) == "Hello!"
    assert resolve_placeholders(body, {"country": "Montenegro"}) == "Poštovani!"


# ── list_placeholders + unknown/unresolved detectors ────────────────────────

def test_list_placeholders_ordered_unique():
    body = "{{first_name}} x {{company_name}} y {{first_name}}"
    assert list_placeholders(body) == ["first_name", "company_name"]

def test_find_unknown_placeholders():
    body = "{{first_name}} {{foo}} {{bar}}"
    assert find_unknown_placeholders(body) == ["foo", "bar"]

def test_find_unresolved_for_recipient_missing_company():
    body = "{{salutation}}, {{company_name}}"
    person = {"first_name": "Marko"}
    assert find_unresolved_for_recipient(body, person) == ["company_name"]

def test_find_unresolved_ignores_unknown_placeholders():
    body = "{{salutation}} {{xyz}}"
    # Only KNOWN-but-empty count; unknown placeholders are a different concern
    assert find_unresolved_for_recipient(body, {}) == []


# ── scenarios from the plan ─────────────────────────────────────────────────

def test_three_previews_differ():
    """The Slack preview shows 3 rendered examples — they should look distinct."""
    body = "{{salutation}}, from {{company_name}}"
    r1 = resolve_placeholders(body, {"salutation": "Draga Leonarda", "company_name": "Chedi"})
    r2 = resolve_placeholders(body, {"first_name": "Vladimir", "language": "Montenegrin", "company_name": "Regent"})
    r3 = resolve_placeholders(body, {"first_name": "Marko", "language": "English", "company_name": "Splendid"})
    assert r1 == "Draga Leonarda, from Chedi"
    assert r2 == "Poštovani Vladimir, from Regent"
    assert r3 == "Dear Marko, from Splendid"
