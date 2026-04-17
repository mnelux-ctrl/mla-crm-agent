"""Unit tests for domain/campaign.py state machine (no I/O)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from airtable.schema import CampaignStatus


# ── CampaignStatus transitions ──────────────────────────────────────────────

def test_drafting_can_go_to_awaiting_approval():
    assert CampaignStatus.can_transition(CampaignStatus.DRAFTING, CampaignStatus.AWAITING_APPROVAL)


def test_drafting_can_be_cancelled():
    assert CampaignStatus.can_transition(CampaignStatus.DRAFTING, CampaignStatus.CANCELLED)


def test_cannot_skip_drafting_to_sending():
    assert not CampaignStatus.can_transition(CampaignStatus.DRAFTING, CampaignStatus.SENDING)


def test_awaiting_approval_to_approved():
    assert CampaignStatus.can_transition(CampaignStatus.AWAITING_APPROVAL, CampaignStatus.APPROVED)


def test_awaiting_approval_back_to_drafting_ok():
    """Allow Stefan to re-edit after generating drafts."""
    assert CampaignStatus.can_transition(CampaignStatus.AWAITING_APPROVAL, CampaignStatus.DRAFTING)


def test_approved_to_sending():
    assert CampaignStatus.can_transition(CampaignStatus.APPROVED, CampaignStatus.SENDING)


def test_sending_to_paused_to_sending():
    assert CampaignStatus.can_transition(CampaignStatus.SENDING, CampaignStatus.PAUSED)
    assert CampaignStatus.can_transition(CampaignStatus.PAUSED, CampaignStatus.SENDING)


def test_sending_to_completed():
    assert CampaignStatus.can_transition(CampaignStatus.SENDING, CampaignStatus.COMPLETED)


def test_completed_is_terminal():
    for target in CampaignStatus.ALL:
        assert not CampaignStatus.can_transition(CampaignStatus.COMPLETED, target)


def test_cancelled_is_terminal():
    for target in CampaignStatus.ALL:
        assert not CampaignStatus.can_transition(CampaignStatus.CANCELLED, target)


def test_cannot_go_backward_from_sending_to_approved():
    """Once sending started, can only pause/complete/cancel."""
    assert not CampaignStatus.can_transition(CampaignStatus.SENDING, CampaignStatus.APPROVED)
    assert not CampaignStatus.can_transition(CampaignStatus.SENDING, CampaignStatus.AWAITING_APPROVAL)


# ── Campaign dataclass roundtrip ────────────────────────────────────────────

def test_campaign_to_from_dict_roundtrip():
    """Ensure to_dict/from_dict is lossless (important for Redis hot state)."""
    # Import here to avoid triggering config.validate_all at module load
    import os
    os.environ.setdefault("CRM_API_KEY", "x")
    os.environ.setdefault("CRM_INTERNAL_KEY", "x")
    os.environ.setdefault("AIRTABLE_PAT", "x")
    os.environ.setdefault("REDIS_URL", "redis://localhost")
    os.environ.setdefault("EMAIL_AGENT_URL", "http://x")
    os.environ.setdefault("EMAIL_AGENT_INTERNAL_KEY", "x")

    from domain.campaign import Campaign, new_campaign_id

    c = Campaign(
        campaign_id=new_campaign_id(),
        name="Test",
        template_name="Hello",
        segment_filter_json={"entity_type": ["Hotel"]},
        master_subject="Hi {{first_name}}",
        master_body="Dear {{first_name}}, welcome.",
        attachments=["https://example.com/doc.pdf"],
        recipients=[
            {"person_id": "recA", "email": "a@x.com", "status": "scheduled"},
            {"person_id": "recB", "email": "b@x.com", "status": "scheduled"},
        ],
        status=CampaignStatus.AWAITING_APPROVAL,
        approval_token="",
        sent_count=0,
        org_id="mla",
    )

    d = c.to_dict()
    c2 = Campaign.from_dict(d)

    assert c.campaign_id == c2.campaign_id
    assert c.name == c2.name
    assert c.master_subject == c2.master_subject
    assert c.recipients == c2.recipients
    assert c.status == c2.status
    assert c.segment_filter_json == c2.segment_filter_json


def test_campaign_transition_enforced():
    import os
    os.environ.setdefault("CRM_API_KEY", "x")
    os.environ.setdefault("CRM_INTERNAL_KEY", "x")
    os.environ.setdefault("AIRTABLE_PAT", "x")
    os.environ.setdefault("REDIS_URL", "redis://localhost")
    os.environ.setdefault("EMAIL_AGENT_URL", "http://x")
    os.environ.setdefault("EMAIL_AGENT_INTERNAL_KEY", "x")

    from domain.campaign import Campaign, new_campaign_id

    c = Campaign(
        campaign_id=new_campaign_id(),
        name="x",
        template_name="t",
        segment_filter_json={},
        master_subject="s",
        master_body="b",
        status=CampaignStatus.DRAFTING,
    )

    c.transition_to(CampaignStatus.AWAITING_APPROVAL)
    assert c.status == CampaignStatus.AWAITING_APPROVAL

    with pytest.raises(ValueError):
        c.transition_to(CampaignStatus.COMPLETED)   # invalid jump


def test_campaign_completed_sets_completed_at():
    import os
    os.environ.setdefault("CRM_API_KEY", "x")
    os.environ.setdefault("CRM_INTERNAL_KEY", "x")
    os.environ.setdefault("AIRTABLE_PAT", "x")
    os.environ.setdefault("REDIS_URL", "redis://localhost")
    os.environ.setdefault("EMAIL_AGENT_URL", "http://x")
    os.environ.setdefault("EMAIL_AGENT_INTERNAL_KEY", "x")

    from domain.campaign import Campaign, new_campaign_id

    c = Campaign(
        campaign_id=new_campaign_id(),
        name="x", template_name="t",
        segment_filter_json={}, master_subject="s", master_body="b",
        status=CampaignStatus.SENDING,
    )
    assert c.completed_at == ""
    c.transition_to(CampaignStatus.COMPLETED)
    assert c.completed_at != ""
