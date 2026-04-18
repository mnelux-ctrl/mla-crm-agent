"""
config.py — Central configuration for MLA CRM Agent (6th Railway service).

Smart outreach brain: campaigns, segmentation, personalization, rate-limited send.
Delegates actual Gmail send to mla-email-agent via /api/internal/send-now with
a campaign approval token.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def _optional(name: str, default: str = "") -> str:
    return os.getenv(name, default)


# Public-facing base URL (for unsubscribe links in emails, SSE, future frontend)
PUBLIC_BASE_URL = ""   # resolved lazily from env below to avoid forward-ref issues


# ── API Security ────────────────────────────────────────────────────────────
CRM_API_KEY: str = _require("CRM_API_KEY")             # Public-ish: COO and future frontend
CRM_INTERNAL_KEY: str = _require("CRM_INTERNAL_KEY")   # Service-to-service: email-agent callbacks

# ── Airtable ────────────────────────────────────────────────────────────────
AIRTABLE_PAT: str = _require("AIRTABLE_PAT")
AIRTABLE_BASE_ID: str = _optional("AIRTABLE_BASE_ID", "appMa1XAD2JXQgrfV")

# Stefan-editable CRM tables (created manually in Airtable per schema-changes.md)
# Contact tables — MLA stores people across 5 segment-specific tables. All share
# the same field names. Comma-separated list, env-overridable.
_DEFAULT_CONTACT_TABLES = ",".join([
    "National Companies",
    "National Producers",
    "National Institutions",
    "International Buyers",
    "International Partners",
])
AIRTABLE_CONTACT_TABLES: list[str] = [
    t.strip() for t in _optional("AIRTABLE_CONTACT_TABLES", _DEFAULT_CONTACT_TABLES).split(",")
    if t.strip()
]

AIRTABLE_EMAIL_LOG_TABLE: str = _optional("AIRTABLE_EMAIL_LOG_TABLE", "EMAIL_LOG")
AIRTABLE_CRM_TEMPLATE_TABLE: str = _optional("AIRTABLE_CRM_TEMPLATE_TABLE", "CRM_TEMPLATE")
AIRTABLE_CRM_SEGMENT_TABLE: str = _optional("AIRTABLE_CRM_SEGMENT_TABLE", "CRM_SEGMENT")
AIRTABLE_CRM_CAMPAIGN_TABLE: str = _optional("AIRTABLE_CRM_CAMPAIGN_TABLE", "CRM_CAMPAIGN")

# ── Redis (shared with other MLA services) ──────────────────────────────────
REDIS_URL: str = _require("REDIS_URL")

# ── Downstream services ─────────────────────────────────────────────────────
EMAIL_AGENT_URL: str = _require("EMAIL_AGENT_URL")     # e.g. https://mla-email-agent-production.up.railway.app
EMAIL_AGENT_INTERNAL_KEY: str = _require("EMAIL_AGENT_INTERNAL_KEY")
SUPERKNOWLEDGE_URL: str = _optional("SUPERKNOWLEDGE_URL", "")  # non-fatal if absent
SUPERKNOWLEDGE_API_KEY: str = _optional("SUPERKNOWLEDGE_API_KEY", "")

# ── AI (optional drafting-assist pass) ──────────────────────────────────────
OPENAI_API_KEY: str = _optional("OPENAI_API_KEY", "")  # optional: used for instructions_override
# gpt-5.4 is the current production model across MLA (same as coo-agent, email-agent).
# Override in env if using a different model.
OPENAI_MODEL: str = _optional("OPENAI_MODEL", "gpt-5.4")

# ── Slack (for single-approval messages to Stefan) ──────────────────────────
SLACK_BOT_TOKEN: str = _optional("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET: str = _optional("SLACK_SIGNING_SECRET", "")
SLACK_STEFAN_USER_ID: str = _optional("SLACK_STEFAN_USER_ID", "")
SLACK_DEFAULT_CHANNEL: str = _optional("SLACK_DEFAULT_CHANNEL", "")  # Stefan DM channel ID

# ── Send safety ─────────────────────────────────────────────────────────────
GMAIL_DAILY_LIMIT: int = int(_optional("GMAIL_DAILY_LIMIT", "1500"))   # headroom under 2000 Workspace cap
MIN_GAP_SECONDS: int = int(_optional("MIN_GAP_SECONDS", "90"))         # absolute floor, regardless of batch size

# ── Multi-tenancy (single-tenant MLA for now; prep for SaaS) ────────────────
DEFAULT_ORG_ID: str = _optional("DEFAULT_ORG_ID", "mla")

# ── Infrastructure ──────────────────────────────────────────────────────────
PORT: int = int(_optional("PORT", "8005"))   # 5 services already: email, coo, admin, team-manager, superknowledge
ENV: str = _optional("ENV", "production")
TIMEZONE: str = _optional("TIMEZONE", "Europe/Podgorica")
PUBLIC_BASE_URL = _optional("PUBLIC_BASE_URL", "https://mla-crm-agent-production.up.railway.app")

# ── Sender identity (for template {{sender_name}} / {{sender_title}} fallback) ──
DEFAULT_SENDER_NAME: str = _optional("DEFAULT_SENDER_NAME", "Stefan Stešević")
DEFAULT_SENDER_TITLE: str = _optional("DEFAULT_SENDER_TITLE", "Founder & Executive Director")
DEFAULT_EVENT_NAME: str = _optional("DEFAULT_EVENT_NAME", "LTSM 2026")


def validate_all() -> None:
    required = [
        "CRM_API_KEY", "CRM_INTERNAL_KEY",
        "AIRTABLE_PAT", "REDIS_URL",
        "EMAIL_AGENT_URL", "EMAIL_AGENT_INTERNAL_KEY",
    ]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError(
            "mla-crm-agent cannot start. Missing env vars:\n"
            + "\n".join(f"  - {k}" for k in missing)
        )
