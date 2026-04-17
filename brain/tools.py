"""
brain/tools.py — OpenAI function-calling schemas for the CRM brain.

These tools let both Stefan (via direct CRM Slack DM) and other agents
(via COO delegation) command the CRM. Tool definitions mirror the
semantics used by domain.segment / domain.template / api.campaigns, but
the dispatch runs locally inside this service (no HTTP hop).
"""

from __future__ import annotations

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_templates",
            "description": "List all CRM email templates available in Airtable. Call first when Stefan asks about available templates or is unsure which to use.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_segments",
            "description": "List all saved CRM segments (named filters) in Airtable.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preview_segment",
            "description": (
                "Preview recipients for a segment: count + 5 examples, NO send. Use when Stefan asks "
                "'koliko X imamo', 'daj mi listu X', 'ko su naši X', before launching any campaign. "
                "Either segment_name or filter_json is required."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "segment_name": {"type": "string", "description": "Name of saved CRM_SEGMENT"},
                    "filter_json": {
                        "type": "object",
                        "description": (
                            "Ad-hoc filter. Allowed keys: country (list), ltsm_role (list — Exhibitor/Hosted Buyer/Speaker/Media/Sponsor/...), "
                            "ltsm_2025_status (list), ltsm_2026_status (list — Confirmed/Pending/Interested/Declined), "
                            "company_type (list — Hotel/Resort/DMC/...), partner_type (list), city (list), role_title (list), company (list), "
                            "language ('English'/'Montenegrin'/'any'), gender ('Male'/'Female'/'any'), "
                            "vip_flag (bool), priority (list), relationship_stage (list), "
                            "exclude_do_not_contact (bool, default true), "
                            "exclude_contacted_within_days (int 0-365, anti-dup), "
                            "exclude_in_campaign (list[str] of campaign_id, anti-dup), "
                            "cc_role_title (list[str], OPT-IN auto-CC same-company colleagues by title), "
                            "cc_ltsm_role (list[str], OPT-IN auto-CC by LTSM role). "
                            "Event targeting uses ltsm_role (Exhibitor/Hosted Buyer/Speaker/Media/Sponsor) "
                            "combined with ltsm_2026_status (Confirmed/Pending/Interested) for current year."
                        ),
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch_campaign",
            "description": (
                "Launch a segmented bulk outreach campaign. Uses a template + filter/segment to send personalized emails "
                "to the whole group with rate-limited gaps (2-6 min) to avoid Gmail ban. Posts ONE Slack approval message "
                "with 3 previews. Only sends after Stefan clicks Approve. "
                "Supports scheduled_start_at (ISO datetime) to begin sending at a future time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Friendly campaign name e.g. 'LTSM 2026 Exhibitor Welcome — Wave 1'"},
                    "template_name": {"type": "string", "description": "Name of CRM_TEMPLATE"},
                    "segment_name": {"type": "string", "description": "Name of saved segment (optional — use filter_json otherwise)"},
                    "filter_json": {"type": "object", "description": "Ad-hoc filter (see preview_segment for allowed keys)"},
                    "instructions_override": {"type": "string", "description": "Optional free-form GPT tweak to the template body"},
                    "scheduled_start_at": {
                        "type": "string",
                        "description": (
                            "Optional ISO-8601 datetime with timezone (e.g. '2026-05-12T10:00:00+02:00'). "
                            "When Stefan says 'zakaži za 10 dana u 9h' or 'sutra u 10h', compute absolute datetime and pass here."
                        ),
                    },
                },
                "required": ["template_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "custom_send",
            "description": (
                "Send a campaign where Stefan supplies the body himself (no AI drafting). "
                "Use when Stefan pastes text and says 'pošalji ovo svima'. "
                "Body can contain {{salutation}}, {{first_name}}, {{company_name}}, etc. placeholders."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "subject": {"type": "string"},
                    "body_template": {"type": "string", "description": "Full email body with optional {{placeholders}}"},
                    "segment_name": {"type": "string"},
                    "filter_json": {"type": "object"},
                    "scheduled_start_at": {"type": "string"},
                },
                "required": ["subject", "body_template"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "campaign_status",
            "description": "Get live status of a campaign by ID: sent/failed/reply counts, scheduled start, status (drafting/approved/sending/completed/...).",
            "parameters": {
                "type": "object",
                "properties": {"campaign_id": {"type": "string"}},
                "required": ["campaign_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pause_campaign",
            "description": "Pause an in-flight campaign. Removes remaining scheduled sends. Resumable.",
            "parameters": {
                "type": "object",
                "properties": {"campaign_id": {"type": "string"}},
                "required": ["campaign_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resume_campaign",
            "description": "Resume a paused campaign. Reschedules remaining sends from now with safe gaps.",
            "parameters": {
                "type": "object",
                "properties": {"campaign_id": {"type": "string"}},
                "required": ["campaign_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_campaign",
            "description": "Cancel a campaign. Removes pending sends, revokes approval token. Terminal.",
            "parameters": {
                "type": "object",
                "properties": {"campaign_id": {"type": "string"}},
                "required": ["campaign_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_segment",
            "description": "Save a filter_json as a named segment for reuse. Use when Stefan says 'zapamti ovu grupu kao X'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "filter_json": {"type": "object"},
                },
                "required": ["name", "filter_json"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_to_admin",
            "description": (
                "Delegate document preparation to the Admin Executor AI agent. Use when Stefan wants a memorandum, letter, "
                "agreement or report prepared in Google Docs before sending a campaign with it attached. "
                "Returns the Google Doc URL which you can then pass as attachment_url to launch_campaign."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_type": {"type": "string", "enum": ["memorandum", "letter", "agreement", "report", "proposal"]},
                    "instructions": {"type": "string", "description": "What the document should contain — free-form"},
                    "context": {"type": "string", "description": "Additional context for the admin agent"},
                },
                "required": ["document_type", "instructions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_knowledge",
            "description": "Recall everything the MLA brain knows about a topic across all agents (SuperKnowledge). Use to personalize outreach or fetch past interaction history.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]
