"""
airtable/schema.py — Field name constants for the Airtable CRM tables.

Single source of truth. If Stefan renames a field in Airtable, change it here
in one place and the whole service adapts.

Reality check: MLA does NOT have a single PERSON table. Contacts live across
6 tables (National Companies, National Producers, National Institutions,
International Buyers, International Partners, Friends & Family). All share the
same field names — so ContactField applies to all of them.
"""

from __future__ import annotations


# ── CONTACT fields (shared across all 6 contact tables) ────────────────────

class ContactField:
    FULL_NAME = "full_name"
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    EMAIL_PRIMARY = "email_primary"
    EMAIL_SECONDARY = "email_secondary"
    PHONE = "phone"
    WHATSAPP_NUMBER = "whatsapp_number"
    COMPANY = "company"
    ROLE_TITLE = "role_title"
    COMPANY_TYPE = "company_type"       # on Nat.Companies, Nat.Producers, Int.Buyers
    PARTNER_TYPE = "partner_type"       # on Int.Partners only
    COUNTRY = "country"
    LINKEDIN_URL = "linkedin_url"
    LANGUAGE = "language"
    GENDER = "gender"
    BIRTHDAY = "birthday"
    RELATIONSHIP_STAGE = "relationship_stage"
    PRIORITY = "priority"
    VIP_FLAG = "vip_flag"
    LTSM_ROLE = "ltsm_role"
    LTSM_2025_STATUS = "ltsm_2025_status"
    LTSM_2026_STATUS = "ltsm_2026_status"
    LAST_CONTACT_DATE = "last_contact_date"
    LAST_CONTACT_TOPIC = "last_contact_topic"
    AWAITING_REPLY = "awaiting_reply"
    DO_NOT_CONTACT = "do_not_contact"
    FOLLOW_UP_DUE = "follow_up_due"
    NOTES = "notes"
    EVENT_HISTORY = "event_history"

    # Added by CRM (all 5 outreach tables)
    SALUTATION = "salutation"
    LAST_OUTBOUND_THREAD_ID = "last_outbound_thread_id"
    LAST_OUTBOUND_AT = "last_outbound_at"


# The 5 outreach tables. Friends & Family intentionally excluded (not outreach).
DEFAULT_CONTACT_TABLES: tuple[str, ...] = (
    "National Companies",
    "National Producers",
    "National Institutions",
    "International Buyers",
    "International Partners",
)


# Legacy alias — keep for import compatibility while older code migrates.
PersonField = ContactField


# ── EMAIL_LOG (aka COMMUNICATION_LOG) ───────────────────────────────────────

class EmailLogField:
    EMAIL_SUBJECT = "email_subject"
    LOG_DATE = "log_date"
    DIRECTION = "direction"
    EMAIL_FROM = "email_from"
    EMAIL_TO = "email_to"
    SEND_AS = "send_as"
    GMAIL_THREAD_ID = "gmail_thread_id"
    GMAIL_MESSAGE_ID = "gmail_message_id"
    EMAIL_TYPE = "email_type"
    CLASSIFICATION = "classification"
    DECISION = "decision"
    DRAFT_FINAL = "draft_final"
    CONTACT_LINK = "contact"            # existing link field
    PROJECT_LINK = "project"            # existing link field

    # Added by CRM
    CAMPAIGN_ID = "campaign_id"
    SCHEDULED_SEND_AT = "scheduled_send_at"
    SEND_ERROR = "send_error"


# ── CRM_TEMPLATE ────────────────────────────────────────────────────────────

class TemplateField:
    NAME = "name"
    CHANNEL = "channel"
    LANGUAGE = "language"
    SUBJECT_TEMPLATE = "subject_template"
    BODY_TEMPLATE = "body_template"
    ATTACHMENT_URLS = "attachment_urls"
    PARTNER_CATEGORY = "partner_category"
    IS_ARCHIVED = "is_archived"
    ORG_ID = "org_id"


# ── CRM_SEGMENT ─────────────────────────────────────────────────────────────

class SegmentField:
    NAME = "name"
    DESCRIPTION = "description"
    FILTER_JSON = "filter_json"
    SOURCE_TABLE = "source_table"
    CACHED_RECIPIENT_COUNT = "cached_recipient_count"
    CACHED_AT = "cached_at"
    IS_ARCHIVED = "is_archived"
    ORG_ID = "org_id"


# ── CRM_CAMPAIGN ────────────────────────────────────────────────────────────

class CampaignField:
    CAMPAIGN_ID = "campaign_id"
    NAME = "name"
    STATUS = "status"
    RECIPIENT_COUNT = "recipient_count"
    SENT_COUNT = "sent_count"
    FAILED_COUNT = "failed_count"
    REPLY_COUNT = "reply_count"
    SCHEDULED_START_AT = "scheduled_start_at"
    APPROVAL_TOKEN = "approval_token"
    APPROVED_SLACK_TS = "approved_slack_ts"
    CREATED_AT = "created_at"
    COMPLETED_AT = "completed_at"
    MASTER_DRAFT_SUBJECT = "master_draft_subject"
    MASTER_DRAFT_BODY = "master_draft_body"
    SEGMENT_NAME = "segment_name"
    TEMPLATE_NAME = "template_name"
    ORG_ID = "org_id"


# ── Campaign state values ───────────────────────────────────────────────────

class CampaignStatus:
    DRAFTING = "drafting"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    SENDING = "sending"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    ALL = frozenset({
        DRAFTING, AWAITING_APPROVAL, APPROVED, SENDING,
        PAUSED, COMPLETED, CANCELLED,
    })

    TRANSITIONS = {
        DRAFTING: {AWAITING_APPROVAL, CANCELLED},
        AWAITING_APPROVAL: {APPROVED, CANCELLED, DRAFTING},
        APPROVED: {SENDING, CANCELLED},
        SENDING: {PAUSED, COMPLETED, CANCELLED},
        PAUSED: {SENDING, CANCELLED},
        COMPLETED: set(),
        CANCELLED: set(),
    }

    @classmethod
    def can_transition(cls, from_status: str, to_status: str) -> bool:
        return to_status in cls.TRANSITIONS.get(from_status, set())
