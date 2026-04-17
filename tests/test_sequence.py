"""Tests for sequence (drip campaign) engine."""

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


# ── SequenceStatus enum ─────────────────────────────────────────────────────

def test_sequence_status_values():
    from airtable.schema import SequenceStatus
    assert SequenceStatus.ACTIVE == "active"
    assert SequenceStatus.PAUSED_REPLY == "paused_reply"
    assert SequenceStatus.PAUSED_MANUAL == "paused_manual"
    assert SequenceStatus.COMPLETED == "completed"
    assert SequenceStatus.CANCELLED == "cancelled"


# ── ContactField / TemplateField sequence constants ────────────────────────

def test_contact_field_sequence_constants():
    from airtable.schema import ContactField
    assert ContactField.ACTIVE_SEQUENCE == "active_sequence"
    assert ContactField.SEQUENCE_STEP == "sequence_step"
    assert ContactField.SEQUENCE_NEXT_AT == "sequence_next_at"
    assert ContactField.SEQUENCE_STATUS == "sequence_status"
    assert ContactField.LAST_SENT_TEMPLATE == "last_sent_template"


def test_template_field_sequence_constants():
    from airtable.schema import TemplateField
    assert TemplateField.SEQUENCE_NAME == "sequence_name"
    assert TemplateField.SEQUENCE_STEP == "sequence_step"
    assert TemplateField.SEQUENCE_DELAY_DAYS == "sequence_delay_days"


# ── load_sequence ───────────────────────────────────────────────────────────

def test_load_sequence_empty_name():
    from domain.sequence import load_sequence
    assert load_sequence("") == []


def test_load_sequence_returns_sorted():
    from domain import sequence as seq_mod
    fake_rows = [
        {"id": "rec3", "fields": {"name": "Step 3", "sequence_step": 3, "sequence_delay_days": 10, "subject_template": "s3", "body_template": "b3"}},
        {"id": "rec1", "fields": {"name": "Step 1", "sequence_step": 1, "sequence_delay_days": 0, "subject_template": "s1", "body_template": "b1"}},
        {"id": "rec2", "fields": {"name": "Step 2", "sequence_step": 2, "sequence_delay_days": 4, "subject_template": "s2", "body_template": "b2"}},
    ]
    mock_api = MagicMock()
    mock_table = MagicMock()
    mock_table.all.return_value = fake_rows
    mock_api.table.return_value = mock_table

    with patch("domain.sequence.airtable_client._get_api", return_value=mock_api):
        steps = seq_mod.load_sequence("Test Seq")
    assert [s["sequence_step"] for s in steps] == [1, 2, 3]
    assert steps[0]["name"] == "Step 1"
    assert steps[2]["sequence_delay_days"] == 10


def test_load_sequence_skips_rows_missing_step():
    from domain import sequence as seq_mod
    fake_rows = [
        {"id": "rec1", "fields": {"name": "Standalone", "subject_template": "s", "body_template": "b"}},  # no sequence_step
        {"id": "rec2", "fields": {"name": "Step 1", "sequence_step": 1, "subject_template": "s", "body_template": "b"}},
    ]
    mock_api = MagicMock()
    mock_table = MagicMock()
    mock_table.all.return_value = fake_rows
    mock_api.table.return_value = mock_table

    with patch("domain.sequence.airtable_client._get_api", return_value=mock_api):
        steps = seq_mod.load_sequence("X")
    assert len(steps) == 1
    assert steps[0]["name"] == "Step 1"


# ── enroll_in_sequence ──────────────────────────────────────────────────────

def test_enroll_missing_sequence_returns_error():
    from domain import sequence as seq_mod
    with patch.object(seq_mod, "load_sequence", return_value=[]):
        result = seq_mod.enroll_in_sequence(
            [{"airtable_id": "recA", "source_table": "National Companies", "email": "a@x.com"}],
            "NonExistent",
        )
    assert result["sequence_missing"] == 1
    assert result["enrolled"] == 0


def test_enroll_writes_correct_state():
    from domain import sequence as seq_mod
    fake_steps = [
        {"airtable_id": "rec1", "name": "Step 1", "sequence_step": 1, "sequence_delay_days": 0, "subject_template": "s", "body_template": "b", "attachment_urls": []},
    ]
    with patch.object(seq_mod, "load_sequence", return_value=fake_steps):
        with patch.object(seq_mod, "write_contact_state") as mock_write:
            # Mock the "skip already enrolled" check — make it look empty
            mock_api = MagicMock()
            mock_table = MagicMock()
            mock_table.get.return_value = {"fields": {}}
            mock_api.table.return_value = mock_table
            with patch("domain.sequence.airtable_client._get_api", return_value=mock_api):
                result = seq_mod.enroll_in_sequence(
                    [{"airtable_id": "recA", "source_table": "National Companies", "email": "a@x.com"}],
                    "Test Seq",
                )
            assert result["enrolled"] == 1
            mock_write.assert_called_once()
            kwargs = mock_write.call_args.kwargs
            assert kwargs["active_sequence"] == "Test Seq"
            assert kwargs["sequence_step"] == 0  # enrolled but step 1 not yet sent
            assert kwargs["sequence_status"] == "active"


def test_enroll_skips_already_enrolled():
    from domain import sequence as seq_mod
    from airtable.schema import SequenceStatus
    fake_steps = [{"airtable_id": "rec1", "name": "Step 1", "sequence_step": 1, "sequence_delay_days": 0, "subject_template": "s", "body_template": "b", "attachment_urls": []}]

    mock_api = MagicMock()
    mock_table = MagicMock()
    # The contact is ALREADY in an active sequence
    mock_table.get.return_value = {"fields": {
        "active_sequence": "Another",
        "sequence_status": SequenceStatus.ACTIVE,
    }}
    mock_api.table.return_value = mock_table

    with patch.object(seq_mod, "load_sequence", return_value=fake_steps):
        with patch.object(seq_mod, "write_contact_state") as mock_write:
            with patch("domain.sequence.airtable_client._get_api", return_value=mock_api):
                result = seq_mod.enroll_in_sequence(
                    [{"airtable_id": "recA", "source_table": "National Companies", "email": "a@x.com"}],
                    "New Seq",
                    skip_already_enrolled=True,
                )
            assert result["enrolled"] == 0
            assert result["skipped_already_in"] == 1
            mock_write.assert_not_called()


def test_enroll_respects_missing_table():
    from domain import sequence as seq_mod
    fake_steps = [{"airtable_id": "rec1", "name": "Step 1", "sequence_step": 1, "sequence_delay_days": 0, "subject_template": "s", "body_template": "b", "attachment_urls": []}]
    with patch.object(seq_mod, "load_sequence", return_value=fake_steps):
        with patch.object(seq_mod, "write_contact_state"):
            result = seq_mod.enroll_in_sequence(
                [{"airtable_id": "recA", "source_table": "", "email": "a@x.com"}],  # no table!
                "Seq",
            )
    assert result["skipped_missing_table"] == 1


# ── pause / resume / cancel ────────────────────────────────────────────────

def test_pause_reason_sets_right_status():
    from domain import sequence as seq_mod
    from airtable.schema import SequenceStatus
    with patch("domain.sequence.airtable_client.fetch_contact_by_email", return_value=({"id": "recA"}, "National Companies")):
        with patch.object(seq_mod, "write_contact_state") as mock_write:
            # reason='reply'
            seq_mod.pause_sequence_for_contact("a@x.com", reason="reply")
            assert mock_write.call_args.kwargs["sequence_status"] == SequenceStatus.PAUSED_REPLY
            # reason='manual'
            mock_write.reset_mock()
            seq_mod.pause_sequence_for_contact("a@x.com", reason="manual")
            assert mock_write.call_args.kwargs["sequence_status"] == SequenceStatus.PAUSED_MANUAL


def test_cancel_clears_active_sequence():
    from domain import sequence as seq_mod
    with patch("domain.sequence.airtable_client.fetch_contact_by_email", return_value=({"id": "recA"}, "National Companies")):
        with patch.object(seq_mod, "write_contact_state") as mock_write:
            seq_mod.cancel_sequence_for_contact("a@x.com")
            assert mock_write.call_args.kwargs["active_sequence"] == ""
            assert mock_write.call_args.kwargs["sequence_status"] == "cancelled"


def test_pause_contact_not_found():
    from domain import sequence as seq_mod
    with patch("domain.sequence.airtable_client.fetch_contact_by_email", return_value=(None, None)):
        result = seq_mod.pause_sequence_for_contact("ghost@x.com")
    assert result["ok"] is False
