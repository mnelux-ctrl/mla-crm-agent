# Airtable Schema Changes — Stefan's Checklist

Before `mla-crm-agent` first runs in production, apply these to Airtable base
`appMa1XAD2JXQgrfV`. Everything is additive — no fields are removed or renamed.

---

## 1. Extend PERSON table (add 5 fields)

Open your existing **PERSON** table and add:

| Field name | Type | Options | Why |
|---|---|---|---|
| `salutation` | Single line text | — | **Master override.** You fill per-person: "Draga Marijana", "Poštovani g-dine Petroviću", "Dear Isabel". CRM uses this verbatim when set; auto-falls back to language-aware default when empty. |
| `role_category` | Single select | Sales, Marketing, Operations, Finance, Director, C-Level, Other | Filter by business function (e.g. "hotels with Sales/Marketing roles"). |
| `entity_type` | Single select | Hotel, DMC, Airline, Ministry, Media, Embassy, Exhibitor, Sponsor, Partner, Other | Fastest way to filter "all hotels" / "all exhibitors" without joining COMPANY. |
| `last_outbound_thread_id` | Single line text | — | Quick lookup of our last email thread with this person. Helps COO reply in-thread. |
| `last_outbound_at` | Date & time (ISO) | — | "When did we last write to them?" at a glance. |

> **Tip:** For existing records, leave `salutation` empty — CRM's default fallback
> (`Poštovani {first_name}` / `Dear {first_name}` based on language/country) is
> safe. Fill `salutation` manually for VIP/VVIP or when a specific form matters.

---

## 2. Extend EMAIL_LOG table (add 6 fields)

Open your existing **EMAIL_LOG** (a.k.a. COMMUNICATION_LOG) table and add:

| Field name | Type | Options | Why |
|---|---|---|---|
| `campaign_id` | Single line text | — | Links a log row to a CRM campaign (UUID). |
| `gmail_message_id` | Single line text | — | **Canonical** message id. |
| `gmail_thread_id` | Single line text | — | **Canonical** thread id. Mandatory so COO always replies in the correct thread. |
| `recipient_person_id` | Link to another record → PERSON | Allow linking to multiple — leave default (single) | Bidirectional traceability. |
| `scheduled_send_at` | Date & time | — | When the send was originally scheduled (may differ from sent_at). |
| `send_error` | Long text | — | Populated if a send failed; empty on success. |

> The older `thread_id` / `message_id` fields (if present) are still populated
> by email-agent for backwards compatibility. CRM reads both.

---

## 3. Create 3 new tables (prefix `CRM_` to keep things tidy)

### 3a. `CRM_TEMPLATE`

Master email/WhatsApp/LinkedIn templates with placeholder support.

| Field | Type | Options |
|---|---|---|
| `name` | Single line text (primary) | — |
| `channel` | Single select | email, whatsapp, linkedin |
| `language` | Single select | en, mne, auto |
| `subject_template` | Single line text | — |
| `body_template` | Long text | — |
| `attachment_urls` | Long text | JSON array as string |
| `partner_category` | Single select | Main Strategic, Main International, Official, Sponsor, Government, Hosting, Production, Boutique Exhibitor |
| `is_archived` | Checkbox | — |
| `org_id` | Single line text | default `mla` |

**Example body_template:**

```
{{salutation}},

Pišem vam povodom {{event_name}} — luxury travel summita koji MLA
organizuje u Crnoj Gori. {{company_name}} bi bio savršen partner…

Srdačno,
{{sender_name}}
{{sender_title}}
```

### 3b. `CRM_SEGMENT`

Saved named filters. Reuse across campaigns.

| Field | Type |
|---|---|
| `name` (primary) | Single line text |
| `description` | Long text |
| `filter_json` | Long text (JSON string) |
| `source_table` | Single select: PERSON, COMPANY |
| `cached_recipient_count` | Number |
| `cached_at` | Date & time |
| `is_archived` | Checkbox |
| `org_id` | Single line text (default `mla`) |

**Example filter_json:**

```json
{
  "entity_type": ["Hotel"],
  "role_category": ["Sales", "Marketing"],
  "country": ["Montenegro", "MNE"],
  "exclude_do_not_invite": true
}
```

### 3c. `CRM_CAMPAIGN`

One row per campaign run — audit trail. Hot state lives in Redis.

| Field | Type |
|---|---|
| `campaign_id` (primary) | Single line text (UUID) |
| `name` | Single line text |
| `template_link` | Link to CRM_TEMPLATE |
| `segment_link` | Link to CRM_SEGMENT |
| `status` | Single select: drafting, awaiting_approval, approved, sending, paused, completed, cancelled |
| `recipient_count` | Number |
| `sent_count` | Number |
| `failed_count` | Number |
| `reply_count` | Number |
| `scheduled_start_at` | Date & time |
| `approval_token` | Single line text |
| `approved_slack_ts` | Single line text |
| `created_at` | Date & time |
| `completed_at` | Date & time |
| `master_draft_subject` | Single line text |
| `master_draft_body` | Long text |
| `org_id` | Single line text (default `mla`) |

---

## 4. Slack isolation — CRM gets its OWN bot (important!)

Email agent and CRM agent must NOT share the same Slack DM, otherwise incoming
email drafts and outbound CRM campaign approvals get mixed in the same thread
and it becomes confusing.

**Do this:**

1. Create a NEW Slack app at https://api.slack.com/apps called "MLA CRM Agent"
   (separate from the existing COO/Email bots).
2. Bot Token Scopes: `chat:write`, `chat:write.public`, `im:history`,
   `im:write`, `users:read`, `commands` (optional).
3. Install to workspace → copy the `xoxb-...` Bot Token.
4. Copy the Signing Secret from Basic Information.
5. Enable **Interactivity & Shortcuts** with request URL:
   `https://mla-crm-agent-production.up.railway.app/slack/interactions`
6. Create a dedicated Slack channel `#mla-crm-campaigns` (or use a CRM bot DM)
   and invite the CRM bot to it.
7. Get the channel ID (right-click channel → View details → Copy channel ID at bottom).

Set in `mla-crm-agent` Railway env:
```
SLACK_BOT_TOKEN=xoxb-<CRM bot token — NOT email agent's>
SLACK_SIGNING_SECRET=<CRM app signing secret>
SLACK_STEFAN_USER_ID=<your Slack user ID, same as elsewhere>
SLACK_DEFAULT_CHANNEL=C...<the #mla-crm-campaigns channel ID>
```

Result: CRM approval messages go to `#mla-crm-campaigns`. Email agent drafts
keep going to your email-agent DM. Zero crossed wires.

---

## 5. Event-based targeting — how Stefan speaks maps to filter_json

Stefan's voice command → COO → `delegate_crm_campaign(filter_json=…)`:

| Stefan says | `filter_json` |
|---|---|
| *"svi hoteli u CG sa sales rolama"* | `{entity_type: ["Hotel"], role_category: ["Sales"], country: ["Montenegro"]}` |
| *"svi exhibitori LTSM 2026"* | `{event_name: ["LTSM 2026"], event_role: ["Exhibitor"]}` |
| *"svi buyeri prošlogodišnjeg LTSM-a"* | `{event_name: ["LTSM 2025"], event_role: ["Hosted Buyer"]}` |
| *"svi potvrđeni govornici LTSM 2026"* | `{event_name: ["LTSM 2026"], event_role: ["Speaker"], event_lifecycle_stage: ["Confirmed"]}` |
| *"svi VIP sponzori LTSM 2026"* | `{event_name: ["LTSM 2026"], event_role: ["Sponsor"], is_vip_in_event: true}` |
| *"svi mediji sa engleskim"* | `{event_name: ["LTSM 2025","LTSM 2026"], event_role: ["Media"], language: "English"}` |
| *"hoteli u CG koji su exhibitori LTSM 2026"* | `{entity_type: ["Hotel"], country: ["Montenegro"], event_name: ["LTSM 2026"], event_role: ["Exhibitor"]}` |

**PERSON-scope** keys (entity_type, role_category, country, city, language,
vip_level_min) and **EVENT-scope** keys (event_name, event_role,
event_lifecycle_stage, coming_status, is_vip_in_event) can be freely mixed —
CRM runs two queries and intersects them.

## 6. Future scheduling — zakazi slanje za 25 dana

Stefan says *"zakaži za 12. maja u 10h"* or *"za 25 dana u 9h"*:

- COO converts the relative date to absolute ISO 8601 with Europe/Podgorica timezone
- Passes as `scheduled_start_at` to CRM
- CRM stores it on the campaign, posts Slack approval with a clear *"📅 Scheduled start: 2026-05-12 10:00"* line
- Stefan approves → CRM waits until that moment to begin sending
- First send fires exactly at scheduled_start_at, then rate-limited gaps apply

If Stefan approves without any scheduled time, sending begins immediately.

---

## 7. Env vars to set in Railway

### `mla-crm-agent` (new service)
```
CRM_API_KEY=mla-crm-2026-secure-key-xyz
CRM_INTERNAL_KEY=mla-crm-internal-2026-secure-xyz
AIRTABLE_PAT=<same as other services>
AIRTABLE_BASE_ID=appMa1XAD2JXQgrfV
REDIS_URL=<same Railway Redis>
EMAIL_AGENT_URL=https://mla-email-agent-production.up.railway.app
EMAIL_AGENT_INTERNAL_KEY=<shared secret; same value below>
SUPERKNOWLEDGE_URL=https://mla-superknowledge-production.up.railway.app
SUPERKNOWLEDGE_API_KEY=mla-sk-2026-secure-key-xyz
OPENAI_API_KEY=<same as other services>
SLACK_BOT_TOKEN=<new or reuse COO bot token>
SLACK_SIGNING_SECRET=<matching secret>
SLACK_STEFAN_USER_ID=U...
SLACK_DEFAULT_CHANNEL=<Stefan DM channel ID>
GMAIL_DAILY_LIMIT=1500
TIMEZONE=Europe/Podgorica
```

### `mla-email-agent` — add 3 new env vars
```
EMAIL_AGENT_INTERNAL_KEY=<same value as CRM side>
CRM_AGENT_URL=https://mla-crm-agent-production.up.railway.app
CRM_INTERNAL_KEY=<same value as CRM side>
```

### `mla-coo-agent` — add 2 new env vars
```
CRM_AGENT_URL=https://mla-crm-agent-production.up.railway.app
CRM_API_KEY=<same as CRM side>
```

---

## 8. Post-setup smoke test (you can do it)

1. Check `mla-crm-agent` health: `GET /health` → should return `{"ok": true, "redis": true}`.
2. In Airtable, create ONE template `CRM_TEMPLATE`: name=`Test Internal`, channel=`email`, subject_template=`Test od CRM-a`, body_template=`{{salutation}},\n\nOvo je test CRM-a.\n\nPozdrav,\n{{sender_name}}`.
3. Add 3 PERSON rows with your own internal MLA emails (e.g. stefan@, ana@, tim@mla). Set `entity_type`=`Other`, `salutation`=`Pozdrav`.
4. Via Slack, message COO: *"pošalji test campaign na sve PERSON sa entity_type=Other, template Test Internal"*.
5. COO should call `delegate_crm_campaign`, CRM posts 1 Slack approval message with 3 previews.
6. Click Approve. 3 emails arrive with ~2-min gaps. Each in a different thread. EMAIL_LOG has 3 new rows with `gmail_message_id`, `gmail_thread_id`, `campaign_id`.
7. Reply to one of them — `CRM_CAMPAIGN.reply_count` should become 1 within a minute.

If steps 1-7 pass, the system is production-ready.
