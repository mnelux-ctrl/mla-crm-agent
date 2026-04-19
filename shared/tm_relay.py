"""
shared/tm_relay.py — Team Manager relay client.

Every MLA specialist → Stefan notification funnels through Team Manager's
`/api/tm/receive-agent-report` endpoint. Each agent vendors an identical
copy of this module (enforced by monorepo when migrated; for now, the
file is byte-identical across all 11 agents — see MLA_SHARED_PHASE_B_PLAN.md).

## Required config

The caller's `config.py` must expose:

    TEAM_MANAGER_URL          str  — e.g. https://mla-team-manager-production.up.railway.app
    TEAM_MANAGER_API_KEY      str  — shared secret
    AGENT_NAME                str  — human-readable name for this agent
                                     (e.g. "Administrator", "Viktorija", "Heir")

If any are missing, `relay()` logs a warning and returns a stub dict with
`stub: True` — the service keeps running (fail-open so reports don't
block work, and the stub is greppable in logs).

## Payload spec (Team Manager v2 contract)

```json
{
  "agent":        "<AGENT_NAME>",
  "sub_agent":    "<optional — Marketing Studio uses this>",
  "kind":         "daily_digest|self_report|task_done|question|error|opportunity|progress",
  "title":        "One-line summary",
  "body":         "Longer explanation; Team Manager prepends a routing emoji",
  "severity":     "low|medium|high",
  "audience":     "stefan|team"
}
```

Team Manager responds 200/202 with `{relayed: true, ts: <slack_ts>}` on
success, or an error dict with `error: "..."`.

## Usage

```python
from shared.tm_relay import relay

await relay(
    kind="daily_digest",
    title="Grant Research — morning scan",
    body=f"Found {n} relevant EU + CG calls. Top 3:\\n\\n{bullets}",
    severity="medium",
)
```

Per `sensible_defaults_pattern.md` Rule 1, specialists NEVER post to
Stefan's DM directly. This module is the ONE exit point.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

import config

logger = logging.getLogger(__name__)


ALLOWED_KINDS = frozenset({
    "daily_digest", "self_report", "task_done",
    "question", "error", "opportunity", "progress",
})
ALLOWED_SEVERITY = frozenset({"low", "medium", "high"})
ALLOWED_AUDIENCE = frozenset({"stefan", "team"})


def _configured() -> bool:
    return bool(
        getattr(config, "TEAM_MANAGER_API_KEY", "")
        and getattr(config, "TEAM_MANAGER_URL", "")
    )


def _agent_name() -> str:
    return (
        getattr(config, "AGENT_NAME", "")
        or getattr(config, "AGENT_DISPLAY_NAME", "")
        or "unknown-agent"
    )


async def relay(
    kind: str,
    title: str,
    body: str,
    severity: str = "medium",
    audience: str = "stefan",
    source_sub_agent: Optional[str] = None,
) -> dict:
    """POST a notification to Team Manager for routing to Stefan.

    Returns one of:
      - `{"relayed": true, "ts": "..."}` on success
      - `{"stub": true, "note": "...", "would_have_sent": {...}}` if TM not configured
      - `{"error": "..."}` on HTTP/network failure
    """
    if kind not in ALLOWED_KINDS:
        logger.warning(
            f"tm_relay: ignoring unknown kind={kind!r}; allowed={sorted(ALLOWED_KINDS)}"
        )
        return {"error": f"unknown kind: {kind}"}

    if severity not in ALLOWED_SEVERITY:
        severity = "medium"
    if audience not in ALLOWED_AUDIENCE:
        audience = "stefan"

    if not _configured():
        logger.warning("tm_relay: TEAM_MANAGER_URL or API_KEY missing — stub reply.")
        return {
            "stub": True,
            "note": "TEAM_MANAGER_URL or TEAM_MANAGER_API_KEY not configured",
            "would_have_sent": {
                "kind": kind, "title": title, "severity": severity,
                "body_chars": len(body or ""),
            },
        }

    url = f"{config.TEAM_MANAGER_URL.rstrip('/')}/api/tm/receive-agent-report"
    headers = {"Authorization": f"Bearer {config.TEAM_MANAGER_API_KEY}"}
    payload = {
        "agent": _agent_name(),
        "sub_agent": source_sub_agent or _agent_name(),
        "kind": kind,
        "title": (title or "")[:250],
        "body": (body or "")[:8000],
        "severity": severity,
        "audience": audience,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code not in (200, 202):
                return {"error": f"TM {r.status_code}: {r.text[:300]}"}
            try:
                return r.json() or {"relayed": True}
            except Exception:
                return {"relayed": True, "note": "TM returned non-JSON body"}
    except Exception as e:
        logger.error(f"tm_relay: request failed: {e}")
        return {"error": str(e)}


def relay_sync(
    kind: str,
    title: str,
    body: str,
    severity: str = "medium",
    audience: str = "stefan",
    source_sub_agent: Optional[str] = None,
) -> dict:
    """Blocking variant for callers not in an async context (e.g. APScheduler
    jobs running in thread pool). Uses httpx.Client.
    """
    if kind not in ALLOWED_KINDS:
        return {"error": f"unknown kind: {kind}"}
    if severity not in ALLOWED_SEVERITY:
        severity = "medium"
    if audience not in ALLOWED_AUDIENCE:
        audience = "stefan"

    if not _configured():
        return {
            "stub": True,
            "note": "TEAM_MANAGER_URL or TEAM_MANAGER_API_KEY not configured",
            "would_have_sent": {
                "kind": kind, "title": title, "severity": severity,
            },
        }

    url = f"{config.TEAM_MANAGER_URL.rstrip('/')}/api/tm/receive-agent-report"
    headers = {"Authorization": f"Bearer {config.TEAM_MANAGER_API_KEY}"}
    payload = {
        "agent": _agent_name(),
        "sub_agent": source_sub_agent or _agent_name(),
        "kind": kind,
        "title": (title or "")[:250],
        "body": (body or "")[:8000],
        "severity": severity,
        "audience": audience,
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(url, json=payload, headers=headers)
            if r.status_code not in (200, 202):
                return {"error": f"TM {r.status_code}: {r.text[:300]}"}
            try:
                return r.json() or {"relayed": True}
            except Exception:
                return {"relayed": True}
    except Exception as e:
        logger.error(f"tm_relay.sync: {e}")
        return {"error": str(e)}
