# MLA CRM Agent — Final Deployment Status

**Date**: 2026-04-17 (autonomous overnight build)
**Status**: Code ready, Airtable ready, tests passing. Awaits Slack app creation + Railway env + deploy.

---

## ✅ What's already done (no action needed)

### Airtable (base `Montenegro Luxury Association Hub` / `appMa1XAD2JXQgrfV`)
- **3 new tables created** via Airtable MCP:
  - `CRM_TEMPLATE` — master templates with placeholder support
  - `CRM_SEGMENT` — saved named filters
  - `CRM_CAMPAIGN` — campaign audit trail
- **15 new fields** added to the 5 outreach contact tables:
  - `salutation`, `last_outbound_thread_id`, `last_outbound_at` × (National Companies, National Producers, National Institutions, International Buyers, International Partners)
- **3 new fields** in `EMAIL_LOG`: `campaign_id`, `scheduled_send_at`, `send_error`
- **3 templates seeded** (ready to use):
  - `LTSM 2026 Exhibitor Welcome`
  - `Hotel Partnership Outreach EN`
  - `Test Internal — CRM Smoke Test`
- **3 segments seeded**:
  - `LTSM 2026 Exhibitors` → filter `{"ltsm_2026_status":["Exhibitor"]}`
  - `LTSM 2025 Buyers` → filter `{"ltsm_role":["Hosted Buyer"],"ltsm_2025_status":["Confirmed","Attended"]}`
  - `Hotels Montenegro` → filter `{"company_type":["Hotel","Resort","Hosting"],"country":["Montenegro","MNE","Crna Gora"]}`

### Code
- `mla-crm-agent/` — new 6th Railway service, fully implemented
- `mla-email-agent/` — patched: `send_authorization` guard + `/api/internal/send-now` + CRM reply webhook
- `mla-coo-agent/` — patched: 4 new CRM tools + dispatch + prompt update
- **108/108 tests passing** across: resolver, scheduler, filter validation, campaign state machine, E2E rendering, API smoke, scheduled start
- All code refactored to match real MLA schema (multi-table contact union, `ltsm_role`/`ltsm_2026_status` event fields, real field names like `gender`, `vip_flag`, `do_not_contact`)

### Safety audit
- `send_email()` grep: exactly 1 definition + 3 authorized call sites (slack_click, scheduled_send, campaign_token) ✓
- AST syntax: 0 errors across 3 services ✓
- FastAPI smoke startup: `/health` returns 200, 21 API routes registered ✓

### Notification sent
- Slack DM to Stefan (channel D04P17XDU4A, ts 1776387399.047329) summarizing progress
- Gmail draft created to info@mnelux.com (Draft ID `r8810332769503423931`) — in Drafts folder, NOT sent

---

## ❗ 3 things Stefan must do (5 min total)

### 1. Create dedicated Slack app "MLA CRM Agent"

I could not do this — it requires your Slack admin login.

1. Go to https://api.slack.com/apps → **Create New App** → From scratch
2. Name: `MLA CRM Agent` · Workspace: `mnelux`
3. **OAuth & Permissions** → Bot Token Scopes:
   - `chat:write`
   - `chat:write.public`
   - `im:history`
   - `im:write`
   - `users:read`
4. **Install to Workspace** → copy `Bot User OAuth Token` (starts with `xoxb-...`)
5. **Basic Information** → copy `Signing Secret`
6. **Interactivity & Shortcuts** → Enable → Request URL: `https://mla-crm-agent-production.up.railway.app/slack/interactions` (activate after Railway deploy)
7. Create channel `#mla-crm-campaigns` in Slack. Invite the CRM bot. Right-click channel → View details → copy **Channel ID** (starts with `C...`).

### 2. Push code + Railway deploy

I've already run `git init` in `mla-crm-agent/`. Run:

```bash
cd "C:/Users/Korisnik/Desktop/MLA/LTSM automatisation/mla-crm-agent"
git add -A
git commit -m "Initial CRM agent — bulk outreach with segmentation, personalization, rate-limit"
gh repo create mnelux-ctrl/mla-crm-agent --private --source=. --push
```

Or use GitHub Desktop. Once on GitHub:

1. Railway → New Service → Deploy from GitHub → select `mla-crm-agent`
2. Add env vars (see below)
3. Generate public domain → copy it, update Slack Interactivity URL

### 3. Railway env vars

**On new service `mla-crm-agent`:**
```
CRM_API_KEY=mla-crm-2026-secure-key-xyz
CRM_INTERNAL_KEY=mla-crm-internal-2026-secure-xyz
AIRTABLE_PAT=<same as other services>
AIRTABLE_BASE_ID=appMa1XAD2JXQgrfV
REDIS_URL=<shared Railway Redis URL from other services>
EMAIL_AGENT_URL=https://mla-email-agent-production.up.railway.app
EMAIL_AGENT_INTERNAL_KEY=mla-email-internal-2026-secure-xyz
SUPERKNOWLEDGE_URL=https://mla-superknowledge-production.up.railway.app
SUPERKNOWLEDGE_API_KEY=mla-sk-2026-secure-key-xyz
OPENAI_API_KEY=<same as other services>
OPENAI_MODEL=gpt-5.4
SLACK_BOT_TOKEN=<from step 1, xoxb-...>
SLACK_SIGNING_SECRET=<from step 1>
SLACK_STEFAN_USER_ID=U04NY9SP6DB
SLACK_DEFAULT_CHANNEL=<channel ID from step 1, C...>
GMAIL_DAILY_LIMIT=1500
TIMEZONE=Europe/Podgorica
DEFAULT_ORG_ID=mla
```

**On existing `mla-email-agent` — add:**
```
EMAIL_AGENT_INTERNAL_KEY=mla-email-internal-2026-secure-xyz
CRM_AGENT_URL=https://mla-crm-agent-production.up.railway.app
CRM_INTERNAL_KEY=mla-crm-internal-2026-secure-xyz
```

**On existing `mla-coo-agent` — add:**
```
CRM_AGENT_URL=https://mla-crm-agent-production.up.railway.app
CRM_API_KEY=mla-crm-2026-secure-key-xyz
```

Then push the email-agent and coo-agent patches from git (see `git status` in those folders).

---

## How to test (after deploy)

1. Verify CRM health: `curl https://mla-crm-agent-production.up.railway.app/health`
2. In Slack, message COO bot: *"koliko hotela imamo u CG?"* → COO should call `preview_crm_segment` → respond with count from `Hotels Montenegro` segment
3. Verbal campaign test: *"pošalji test campaign na svoj info@mnelux.com email koristeći Test Internal template"* → CRM posts Slack approval to `#mla-crm-campaigns` → click Approve → email arrives
4. Production campaign: *"draftuj email za sve exhibitore LTSM 2026"* → uses `LTSM 2026 Exhibitors` segment + `LTSM 2026 Exhibitor Welcome` template

## Open items (non-blocking, come back to later)

- Add `gender` value to `salutation` field for the existing 91 Business Partners — so the Montenegrin greeting auto-resolves correctly. I've left this for you because it's judgment-based and per-person.
- Consider creating an `EVENT` reference table if you want to track events beyond LTSM 2025/2026 — currently event targeting works purely via denormalized `ltsm_*` columns on each contact, which is fine for the two LTSM editions.
- The 6th contact table (`Friends & Family`) is intentionally excluded from outreach. Change `AIRTABLE_CONTACT_TABLES` env var if you ever want it included.

---

## Summary — TL;DR

Everything I could autonomously do is done. Airtable is set up. Code is tested. Templates + segments are seeded. You have:
- A Slack DM with progress report
- A Gmail draft in your Drafts folder as the smoke-test payload
- This file with the 3 remaining manual steps

Total time left for you: ~5 min (Slack app) + ~5 min (Railway env vars) + auto-deploy.
Then you can voice-trigger campaigns from Slack.
