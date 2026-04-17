"""
API smoke tests — use FastAPI TestClient to hit the actual endpoints without
a real Redis or Airtable. We mock just enough.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Set required env vars BEFORE importing config
os.environ.setdefault("CRM_API_KEY", "test-crm-key")
os.environ.setdefault("CRM_INTERNAL_KEY", "test-internal-key")
os.environ.setdefault("AIRTABLE_PAT", "test-pat")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("EMAIL_AGENT_URL", "http://test-email-agent")
os.environ.setdefault("EMAIL_AGENT_INTERNAL_KEY", "test-email-key")
os.environ.setdefault("AIRTABLE_BASE_ID", "appTestBaseXX")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

# Patch Redis out BEFORE main import — we don't need a real Redis for smoke
_fake_redis = MagicMock()
_fake_redis.ping.return_value = True
_fake_redis.get.return_value = None
_fake_redis.smembers.return_value = set()


@pytest.fixture
def client():
    with patch("state.redis_client.get_client", return_value=_fake_redis):
        with patch("state.redis_client.ping", return_value=True):
            # Also patch APScheduler start so tests don't spawn real threads
            with patch("sending.runner.get_scheduler") as mock_sched:
                mock_sched.return_value = MagicMock()
                import main
                with TestClient(main.app) as c:
                    yield c


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["service"] == "mla-crm-agent"


def test_root_endpoint(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["service"] == "mla-crm-agent"


def test_auth_required_on_segments_preview(client):
    resp = client.post("/api/crm/segments/preview", json={"filter_json": {}})
    assert resp.status_code == 422 or resp.status_code == 401


def test_invalid_bearer_rejected(client):
    resp = client.post(
        "/api/crm/segments/preview",
        headers={"Authorization": "Bearer WRONG"},
        json={"filter_json": {"entity_type": ["Hotel"]}},
    )
    assert resp.status_code == 403


def test_segments_preview_with_auth_validates_filter(client):
    """With valid auth but a bad filter key, we should get a 400."""
    with patch("airtable.client.fetch_persons_by_formula", return_value=[]):
        resp = client.post(
            "/api/crm/segments/preview",
            headers={"Authorization": "Bearer test-crm-key"},
            json={"filter_json": {"UNKNOWN_FIELD": "x"}},
        )
        assert resp.status_code == 400
        assert "Unknown filter keys" in resp.json()["detail"]


def test_segments_preview_valid_filter_hits_airtable(client):
    """Well-formed filter should reach the Airtable mock and return ok envelope."""
    fake_rows = [
        {"id": "rec1", "_source_table": "National Companies", "fields": {
            "first_name": "Leonarda", "last_name": "Dedić",
            "email_primary": "l@test.com",
            "company": "Chedi Luštica Bay",
            "ltsm_role": "Exhibitor",
            "ltsm_2026_status": "Confirmed",
            "country": "Montenegro",
        }},
    ]
    with patch("airtable.client.fetch_contacts_by_formula", return_value=fake_rows):
        resp = client.post(
            "/api/crm/segments/preview",
            headers={"Authorization": "Bearer test-crm-key"},
            json={"filter_json": {"ltsm_role": ["Exhibitor"]}},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["count"] == 1
    assert len(body["data"]["examples"]) == 1
    assert body["data"]["examples"][0]["first_name"] == "Leonarda"


def test_verify_token_requires_internal_key(client):
    """verify-token endpoint uses CRM_INTERNAL_KEY, not CRM_API_KEY."""
    resp = client.get(
        "/api/crm/campaigns/fake-id/verify-token?token=xyz",
        headers={"Authorization": "Bearer test-crm-key"},  # wrong key for this route
    )
    assert resp.status_code == 403


def test_verify_token_invalid_returns_false(client):
    """Valid internal auth + nonexistent token returns {valid: false}."""
    _fake_redis.get.return_value = None   # no token mapping
    resp = client.get(
        "/api/crm/campaigns/fake-id/verify-token?token=xyz",
        headers={"Authorization": "Bearer test-internal-key"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"valid": False}


def test_coo_entry_requires_template(client):
    """/api/coo/crm-campaign should 404 when template doesn't exist."""
    with patch("domain.template.load_template", return_value=None):
        resp = client.post(
            "/api/coo/crm-campaign",
            headers={"Authorization": "Bearer test-crm-key"},
            json={"template_name": "Nonexistent", "filter_json": {"entity_type": ["Hotel"]}},
        )
    assert resp.status_code == 404
    assert "Template not found" in resp.json()["detail"]
