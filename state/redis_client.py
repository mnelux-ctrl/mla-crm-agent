"""
state/redis_client.py — Shared Redis connection + namespaced helpers.

Single connection pool. All keys are namespaced by org_id so multi-tenancy
comes for free later.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis

import config

logger = logging.getLogger(__name__)

_pool: redis.ConnectionPool | None = None
_client: redis.Redis | None = None


def get_client() -> redis.Redis:
    """Return singleton sync Redis client using a connection pool."""
    global _pool, _client
    if _client is None:
        _pool = redis.ConnectionPool.from_url(
            config.REDIS_URL,
            decode_responses=True,
            max_connections=20,
        )
        _client = redis.Redis(connection_pool=_pool)
    return _client


def ping() -> bool:
    try:
        return bool(get_client().ping())
    except Exception as e:
        logger.error(f"Redis ping failed: {e}")
        return False


# ── Namespaced key builders ─────────────────────────────────────────────────

def _org(org_id: str | None) -> str:
    return org_id or config.DEFAULT_ORG_ID


def k_campaign(campaign_id: str, org_id: str | None = None) -> str:
    return f"crm:{_org(org_id)}:campaign:{campaign_id}"


def k_token(token: str, org_id: str | None = None) -> str:
    return f"crm:{_org(org_id)}:token:{token}"


def k_events_channel(campaign_id: str, org_id: str | None = None) -> str:
    return f"crm:{_org(org_id)}:events:{campaign_id}"


def k_usage_month(yyyymm: str, org_id: str | None = None) -> str:
    return f"crm:{_org(org_id)}:usage:{yyyymm}"


def k_job_index(campaign_id: str, org_id: str | None = None) -> str:
    """Set of APScheduler job_ids for a campaign (for pause/cancel)."""
    return f"crm:{_org(org_id)}:jobs:{campaign_id}"


# ── High-level helpers ──────────────────────────────────────────────────────

CAMPAIGN_TTL_SECONDS = 14 * 24 * 3600   # 14 days
TOKEN_TTL_SECONDS = 30 * 24 * 3600      # 30 days


def save_campaign_state(campaign_id: str, state: dict[str, Any], org_id: str | None = None) -> None:
    client = get_client()
    client.set(k_campaign(campaign_id, org_id), json.dumps(state), ex=CAMPAIGN_TTL_SECONDS)


def load_campaign_state(campaign_id: str, org_id: str | None = None) -> dict[str, Any] | None:
    client = get_client()
    raw = client.get(k_campaign(campaign_id, org_id))
    return json.loads(raw) if raw else None


def save_approval_token(token: str, campaign_id: str, org_id: str | None = None) -> None:
    client = get_client()
    client.set(k_token(token, org_id), campaign_id, ex=TOKEN_TTL_SECONDS)


def lookup_token(token: str, org_id: str | None = None) -> str | None:
    client = get_client()
    return client.get(k_token(token, org_id))


def revoke_token(token: str, org_id: str | None = None) -> None:
    client = get_client()
    client.delete(k_token(token, org_id))


def add_job_id(campaign_id: str, job_id: str, org_id: str | None = None) -> None:
    client = get_client()
    client.sadd(k_job_index(campaign_id, org_id), job_id)


def remove_job_id(campaign_id: str, job_id: str, org_id: str | None = None) -> None:
    client = get_client()
    client.srem(k_job_index(campaign_id, org_id), job_id)


def list_job_ids(campaign_id: str, org_id: str | None = None) -> list[str]:
    client = get_client()
    return list(client.smembers(k_job_index(campaign_id, org_id)))


def clear_job_index(campaign_id: str, org_id: str | None = None) -> None:
    client = get_client()
    client.delete(k_job_index(campaign_id, org_id))


def publish_event(campaign_id: str, event: dict, org_id: str | None = None) -> None:
    client = get_client()
    client.publish(k_events_channel(campaign_id, org_id), json.dumps(event))


def increment_usage(n: int, org_id: str | None = None) -> int:
    """Increment monthly send counter. Returns new value."""
    from datetime import datetime
    yyyymm = datetime.utcnow().strftime("%Y-%m")
    client = get_client()
    return client.incrby(k_usage_month(yyyymm, org_id), n)
