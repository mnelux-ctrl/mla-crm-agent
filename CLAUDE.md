# MLA CRM Agent

## What This System Is

6th Railway service. Smart outreach CRM for MLA:
- **Segmentation** — filter PERSON/COMPANY by entity_type, role_category, country, language, VIP level, etc.
- **Templates** — single master body with `{{placeholders}}` (`{{salutation}}`, `{{first_name}}`, `{{company_name}}`, ...)
- **Campaigns** — one approval, N rate-limited sends
- **Voice-triggered via COO** — Stefan speaks, COO dispatches, CRM drafts + previews + schedules

CRM does NOT send mail itself. It calls `mla-email-agent/api/internal/send-now` with a
per-campaign `approval_token`. That's the ONLY way send-agent permits programmatic sends.

## Architecture

```
Stefan (voice/text)
   ↓
mla-coo-agent  (tools: delegate_crm_campaign, preview_crm_segment, list_crm_templates, list_crm_segments)
   ↓ POST /api/coo/crm-campaign  (Bearer CRM_API_KEY)
mla-crm-agent
   │
   ├─ fetch PERSON via segment filter → Airtable formula
   ├─ load CRM_TEMPLATE master body
   ├─ (optional) GPT pass with instructions_override to tweak master body
   ├─ validate placeholders resolvable for all recipients
   ├─ render 3 previews + post SINGLE Slack approval message
   │
   └─ (on Stefan Approve)
        ├─ mint approval_token → CRM_CAMPAIGN
        ├─ compute_send_schedule(count, start) → APScheduler date jobs
        └─ each job fires:
             resolve_placeholders(body, person)
                ↓ POST email-agent /api/internal/send-now  (Bearer EMAIL_AGENT_INTERNAL_KEY)
                   {campaign_id, approval_token, to, subject, body, cc, attachments}
             email-agent verifies token with CRM → gmail send_email() → writes EMAIL_LOG
```

## Key Files

| What | Where |
|------|-------|
| Env vars | `config.py` |
| FastAPI app + routes | `main.py` |
| Airtable client | `airtable/client.py` |
| Airtable schema (field name constants) | `airtable/schema.py` |
| Filter JSON → formula translator | `airtable/segments.py` |
| Campaign state machine | `domain/campaign.py` |
| Template load/save | `domain/template.py` |
| Segment load/save/preview | `domain/segment.py` |
| Placeholder resolver (pure, tested) | `personalization/resolver.py` |
| Send schedule math | `sending/scheduler.py` |
| APScheduler runner | `sending/runner.py` |
| HTTP client to email-agent | `sending/email_agent_client.py` |
| API routes | `api/*.py` |
| Slack Block Kit builder | `slack/approval.py` |
| Slack callbacks | `slack/callbacks.py` |
| Redis connection | `state/redis_client.py` |

## Send Safety Contract

**CRM is NOT authorized to send email directly.** Only `mla-email-agent/gmail/client.py::send_email()`
sends. That function has exactly TWO authorized call sites after this service exists:

1. `mla-email-agent/slack/callbacks.py::handle_email_send()` — Stefan clicked button
2. `mla-email-agent/main.py::send_now()` — trusted CRM call with valid `approval_token`

`send_email()` must assert `send_authorization` parameter is non-empty. Any other call path = bug.

## Redis Key Schema

| Key | Type | TTL | Purpose |
|-----|------|-----|---------|
| `crm:{org}:campaign:{id}` | STRING (JSON) | 14d | Hot campaign state |
| `crm:{org}:events:{id}` | PUB/SUB | — | SSE fanout for live progress |
| `crm:{org}:usage:{YYYY-MM}` | COUNTER | ∞ | Send counter (billing prep) |
| `crm:{org}:token:{token}` | STRING | 30d | approval_token → campaign_id reverse lookup |

## Multi-tenancy Prep

All Airtable CRM_* rows have `org_id` (default "mla"). All endpoints honor `X-Org-ID` header
(default DEFAULT_ORG_ID). All Redis keys namespaced by org. Single-tenant MLA today,
SaaS-ready tomorrow — no rewrites.

## Deployment Notes

- Railway project: `surprising-flow`
- Service name: `mla-crm-agent`
- Port: 8005
- Shared Redis: `redis.railway.internal:6379`
- Internal URL for other services: `mla-crm-agent.railway.internal`

## Environment Setup

See `.env.example`. Required env vars: `CRM_API_KEY`, `CRM_INTERNAL_KEY`, `AIRTABLE_PAT`,
`REDIS_URL`, `EMAIL_AGENT_URL`, `EMAIL_AGENT_INTERNAL_KEY`.

## Airtable Schema Prerequisites

Before first run, Stefan must apply the schema changes listed in `docs/airtable-schema.md`:
- PERSON: add `salutation`, `role_category`, `entity_type`, `last_outbound_thread_id`, `last_outbound_at`
- EMAIL_LOG: add `campaign_id`, `gmail_message_id`, `gmail_thread_id`, `recipient_person_id`, `scheduled_send_at`, `send_error`
- New tables: `CRM_TEMPLATE`, `CRM_SEGMENT`, `CRM_CAMPAIGN`

The CRM also READS (no schema changes needed) from the existing:
- `EVENT` — look up event record IDs from name (e.g. "LTSM 2026")
- `EVENT_PARTICIPATION` — participation rows with event_role, lifecycle_stage, coming_status

## Event-Participation Targeting

Stefan frequently targets campaigns by event participation, not just PERSON
properties. The CRM accepts these extra filter keys in `filter_json`:

- `event_name` (list[str]) — ["LTSM 2026", "LTSM 2025", "DEF 2026", …]
- `event_role` (list[str]) — ["Exhibitor", "Hosted Buyer", "Speaker", "Media", "Sponsor"]
- `event_lifecycle_stage` (list[str]) — ["Confirmed", "Pending", "Waitlist", "Declined"]
- `coming_status` (bool) — True → only confirmed attendees
- `is_vip_in_event` (bool)

The CRM resolves EVENT_NAME → record_id, queries EVENT_PARTICIPATION for
matching rows, collects linked PERSON IDs, and intersects with any PERSON-scope
filters. Participants with `exception_flag=True` are ALWAYS excluded.

## Slack Isolation

CRM uses its OWN Slack bot (not email-agent's). Dedicated channel
`#mla-crm-campaigns` (or dedicated DM) keeps campaign approvals separate from
incoming email drafts. See `docs/airtable-schema.md` section 4 for setup.

## Scheduled Campaigns

Campaigns carry `scheduled_start_at` (ISO datetime). If set, Stefan's Approve
click schedules the APScheduler jobs to fire starting at that moment, not now.
Past datetimes are safely rounded up to now (fat-finger protection).
