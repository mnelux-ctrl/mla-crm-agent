"""Tests for reply auto-pause, idempotent approve, anti-duplication filters."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

os.environ.setdefault("CRM_API_KEY", "x")
os.environ.setdefault("CRM_INTERNAL_KEY", "x")
os.environ.setdefault("AIRTABLE_PAT", "x")
os.environ.setdefault("REDIS_URL", "redis://localhost")
os.environ.setdefault("EMAIL_AGENT_URL", "http://x")
os.environ.setdefault("EMAIL_AGENT_INTERNAL_KEY", "x")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


# ── Idempotent approve ──────────────────────────────────────────────────────

def test_idempotent_approve_already_approved():
    from domain import campaign as cmod
    from airtable.schema import CampaignStatus

    c = cmod.Campaign(
        campaign_id="test-1", name="t", template_name="t",
        segment_filter_json={}, master_subject="s", master_body="b",
        status=CampaignStatus.APPROVED,
        approval_token="preexisting-token",
    )

    with patch.object(cmod, "load", return_value=c):
        with patch.object(cmod, "save"):
            with patch.object(cmod.redis_client, "save_approval_token") as mock_save_tok:
                result = cmod.approve("test-1", slack_ts="ts")
                # Should NOT mint a new token since already approved
                mock_save_tok.assert_not_called()
                # Token stays the same
                assert result.approval_token == "preexisting-token"


def test_idempotent_approve_sending_stage():
    """Even if campaign is mid-send, approve should no-op."""
    from domain import campaign as cmod
    from airtable.schema import CampaignStatus

    c = cmod.Campaign(
        campaign_id="test-2", name="t", template_name="t",
        segment_filter_json={}, master_subject="s", master_body="b",
        status=CampaignStatus.SENDING,
        approval_token="token-abc",
    )
    with patch.object(cmod, "load", return_value=c):
        with patch.object(cmod, "save"):
            with patch.object(cmod.redis_client, "save_approval_token") as mock_save_tok:
                result = cmod.approve("test-2", slack_ts="ts")
                mock_save_tok.assert_not_called()


def test_approve_from_awaiting_mints_new_token():
    from domain import campaign as cmod
    from airtable.schema import CampaignStatus

    c = cmod.Campaign(
        campaign_id="test-3", name="t", template_name="t",
        segment_filter_json={}, master_subject="s", master_body="b",
        status=CampaignStatus.AWAITING_APPROVAL,
        approval_token="",
    )
    with patch.object(cmod, "load", return_value=c):
        with patch.object(cmod, "save"):
            with patch.object(cmod.redis_client, "save_approval_token") as mock_save_tok:
                result = cmod.approve("test-3", slack_ts="ts")
                assert result.status == CampaignStatus.APPROVED
                assert result.approval_token  # new token minted
                mock_save_tok.assert_called_once()


# ── Reply auto-pause ────────────────────────────────────────────────────────

def test_increment_reply_marks_specific_person():
    from domain import campaign as cmod
    from airtable.schema import CampaignStatus

    c = cmod.Campaign(
        campaign_id="rc-1", name="t", template_name="t",
        segment_filter_json={}, master_subject="s", master_body="b",
        status=CampaignStatus.SENDING,
        recipients=[
            {"person_id": "recA", "email": "a@x.com", "status": "scheduled"},
            {"person_id": "recB", "email": "b@x.com", "status": "scheduled"},
        ],
    )
    with patch.object(cmod, "load", return_value=c):
        with patch.object(cmod, "save") as mock_save:
            with patch.object(cmod.redis_client, "publish_event"):
                cmod.increment_reply_count("rc-1", person_id="recA")
                # recA marked, recB not
                assert c.recipients[0].get("reply_received") is True
                assert "reply_received" not in c.recipients[1]
                assert c.reply_count == 1


def test_increment_reply_without_person_id_still_counts():
    from domain import campaign as cmod
    from airtable.schema import CampaignStatus

    c = cmod.Campaign(
        campaign_id="rc-2", name="t", template_name="t",
        segment_filter_json={}, master_subject="s", master_body="b",
        status=CampaignStatus.SENDING,
        recipients=[{"person_id": "recA", "email": "a@x.com", "status": "scheduled"}],
    )
    with patch.object(cmod, "load", return_value=c):
        with patch.object(cmod, "save"):
            with patch.object(cmod.redis_client, "publish_event"):
                cmod.increment_reply_count("rc-2")
                assert c.reply_count == 1
                assert "reply_received" not in c.recipients[0]


# ── Anti-duplication filter ─────────────────────────────────────────────────

def test_validate_exclude_contacted_within_days():
    from airtable.segments import validate_filter, FilterValidationError
    validate_filter({"exclude_contacted_within_days": 30})
    validate_filter({"exclude_contacted_within_days": 0})
    validate_filter({"exclude_contacted_within_days": 365})

    with pytest.raises(FilterValidationError):
        validate_filter({"exclude_contacted_within_days": 366})
    with pytest.raises(FilterValidationError):
        validate_filter({"exclude_contacted_within_days": -1})
    with pytest.raises(FilterValidationError):
        validate_filter({"exclude_contacted_within_days": "30"})


def test_formula_includes_dedup_clause():
    from airtable.segments import filter_to_airtable_formula
    formula = filter_to_airtable_formula({
        "country": ["Montenegro"],
        "exclude_contacted_within_days": 14,
    })
    assert "DATETIME_DIFF(NOW(), {last_outbound_at}, 'days') > 14" in formula
    assert "{last_outbound_at}=''" in formula


def test_validate_exclude_in_campaign():
    from airtable.segments import validate_filter, FilterValidationError
    validate_filter({"exclude_in_campaign": ["cid1", "cid2"]})
    validate_filter({"exclude_in_campaign": []})  # empty list OK (no-op)

    with pytest.raises(FilterValidationError):
        validate_filter({"exclude_in_campaign": "cid1"})
    with pytest.raises(FilterValidationError):
        validate_filter({"exclude_in_campaign": [1, 2]})


# ── Daily-limit global counter ──────────────────────────────────────────────

def test_day_send_key_format():
    from state.redis_client import k_day_send
    key = k_day_send("2026-04-17", org_id="mla")
    assert key == "crm:mla:day_send:2026-04-17"
