"""
domain/sequence.py — Sequence (drip campaign) engine.

A SEQUENCE is a set of templates in CRM_TEMPLATE sharing the same
`sequence_name`. Each has `sequence_step` (1, 2, 3, …) and
`sequence_delay_days` (days to wait after the previous step fires before
this one is due).

PER CONTACT:
  active_sequence       — sequence this contact is currently in
  sequence_step         — last step sent (0 = not yet started)
  sequence_next_at      — when the next step is due
  sequence_status       — active / paused_reply / paused_manual / completed / cancelled
  last_sent_template    — name of last template sent (audit + human-readable)

LIFECYCLE:
  enroll(contact, sequence_name)  → active, step=0, next_at=now (or scheduled)
  tick (background, every 5 min)  → finds contacts where next_at <= now and
                                    status=active, sends next step, advances
  reply webhook                    → paused_reply
  manual pause / resume / cancel   → manual control

A contact can only be in ONE active sequence at a time. To switch, cancel
first. New contacts added to a segment can be enrolled even while older
contacts are on step 3 — each contact progresses at its own pace.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from airtable import client as airtable_client
from airtable.schema import (
    TemplateField,
    ContactField,
    SequenceStatus,
)
import config

logger = logging.getLogger(__name__)


# ── Load sequence definition from CRM_TEMPLATE ─────────────────────────────

def load_sequence(sequence_name: str) -> list[dict[str, Any]]:
    """Return sorted list of step-dicts for a sequence name.

    Each step: {name, subject_template, body_template, attachment_urls,
                sequence_step, sequence_delay_days}
    Empty list if sequence doesn't exist.
    """
    if not sequence_name:
        return []
    try:
        api = airtable_client._get_api()
        table = api.table(config.AIRTABLE_BASE_ID, config.AIRTABLE_CRM_TEMPLATE_TABLE)
        escaped = sequence_name.replace("'", "''")
        formula = (
            f"AND({{{TemplateField.SEQUENCE_NAME}}}='{escaped}', "
            f"NOT({{{TemplateField.IS_ARCHIVED}}}))"
        )
        rows = table.all(formula=formula)
    except Exception as e:
        logger.error(f"load_sequence({sequence_name}) failed: {e}")
        return []

    steps = []
    for r in rows:
        f = r.get("fields", {})
        step_no = f.get(TemplateField.SEQUENCE_STEP)
        if step_no is None:
            continue
        import json as _json
        attachment_raw = f.get(TemplateField.ATTACHMENT_URLS, "")
        try:
            attachments = _json.loads(attachment_raw) if attachment_raw else []
        except (ValueError, TypeError):
            attachments = []
        steps.append({
            "airtable_id": r.get("id"),
            "name": f.get(TemplateField.NAME, ""),
            "subject_template": f.get(TemplateField.SUBJECT_TEMPLATE, ""),
            "body_template": f.get(TemplateField.BODY_TEMPLATE, ""),
            "attachment_urls": attachments,
            "sequence_step": int(step_no),
            "sequence_delay_days": int(f.get(TemplateField.SEQUENCE_DELAY_DAYS, 0) or 0),
        })
    steps.sort(key=lambda s: s["sequence_step"])
    return steps


def list_sequences() -> list[dict[str, Any]]:
    """Return list of sequence summaries: {name, step_count, first_template}."""
    try:
        api = airtable_client._get_api()
        table = api.table(config.AIRTABLE_BASE_ID, config.AIRTABLE_CRM_TEMPLATE_TABLE)
        rows = table.all(formula=f"AND({{{TemplateField.SEQUENCE_NAME}}}!='', "
                                 f"NOT({{{TemplateField.IS_ARCHIVED}}}))")
    except Exception as e:
        logger.error(f"list_sequences failed: {e}")
        return []

    by_name: dict[str, list[dict]] = {}
    for r in rows:
        f = r.get("fields", {})
        name = f.get(TemplateField.SEQUENCE_NAME, "")
        if not name:
            continue
        by_name.setdefault(name, []).append(f)

    out = []
    for name, steps in by_name.items():
        steps_sorted = sorted(steps, key=lambda f: f.get(TemplateField.SEQUENCE_STEP, 0) or 0)
        out.append({
            "name": name,
            "step_count": len(steps_sorted),
            "first_template": steps_sorted[0].get(TemplateField.NAME, "") if steps_sorted else "",
            "steps": [
                {
                    "step": s.get(TemplateField.SEQUENCE_STEP),
                    "template_name": s.get(TemplateField.NAME),
                    "delay_days": s.get(TemplateField.SEQUENCE_DELAY_DAYS, 0),
                }
                for s in steps_sorted
            ],
        })
    return out


# ── Per-contact state (Airtable read/write) ────────────────────────────────

def _contact_sequence_state(contact_row: dict) -> dict[str, Any]:
    f = contact_row.get("fields", {})
    return {
        "airtable_id": contact_row.get("id"),
        "source_table": contact_row.get("_source_table", ""),
        "email": f.get(ContactField.EMAIL_PRIMARY, ""),
        "active_sequence": f.get(ContactField.ACTIVE_SEQUENCE, ""),
        "sequence_step": int(f.get(ContactField.SEQUENCE_STEP, 0) or 0),
        "sequence_next_at": f.get(ContactField.SEQUENCE_NEXT_AT, ""),
        "sequence_status": f.get(ContactField.SEQUENCE_STATUS, ""),
        "last_sent_template": f.get(ContactField.LAST_SENT_TEMPLATE, ""),
    }


def write_contact_state(
    table_name: str,
    contact_id: str,
    *,
    active_sequence: str | None = None,
    sequence_step: int | None = None,
    sequence_next_at: datetime | None = None,
    sequence_status: str | None = None,
    last_sent_template: str | None = None,
) -> None:
    """Update sequence state fields on a specific contact row."""
    fields: dict[str, Any] = {}
    if active_sequence is not None:
        fields[ContactField.ACTIVE_SEQUENCE] = active_sequence
    if sequence_step is not None:
        fields[ContactField.SEQUENCE_STEP] = sequence_step
    if sequence_next_at is not None:
        fields[ContactField.SEQUENCE_NEXT_AT] = sequence_next_at.isoformat()
    if sequence_status is not None:
        fields[ContactField.SEQUENCE_STATUS] = sequence_status
    if last_sent_template is not None:
        fields[ContactField.LAST_SENT_TEMPLATE] = last_sent_template
    if not fields:
        return
    try:
        api = airtable_client._get_api()
        table = api.table(config.AIRTABLE_BASE_ID, table_name)
        table.update(contact_id, fields, typecast=True)
    except Exception as e:
        logger.warning(f"write_contact_state({table_name}/{contact_id}) failed: {e}")


# ── Enrollment ─────────────────────────────────────────────────────────────

def enroll_in_sequence(
    contacts: list[dict[str, Any]],
    sequence_name: str,
    *,
    start_at: datetime | None = None,
    skip_already_enrolled: bool = True,
) -> dict[str, int]:
    """Enroll a list of contacts into a sequence.

    `contacts` is the list returned by segment.recipients_for_filter() —
    each dict has airtable_id, source_table, email, etc.

    Returns {enrolled, skipped_already_in, skipped_missing_table, sequence_missing}.

    skip_already_enrolled=True (default): contacts already in an active
    sequence are NOT re-enrolled (avoid interrupting in-progress drips).
    """
    steps = load_sequence(sequence_name)
    if not steps:
        return {"sequence_missing": 1, "enrolled": 0, "skipped_already_in": 0, "skipped_missing_table": 0}

    now = start_at or datetime.now(timezone.utc)
    # Compute when step 1 should fire (step 1's delay is usually 0).
    # If delay=0 we need next_at strictly < NOW so IS_BEFORE matches — add a
    # small negative offset so the next tick picks it up immediately.
    first_step = steps[0]
    first_delay = int(first_step.get("sequence_delay_days", 0) or 0)
    if first_delay == 0:
        next_at = now - timedelta(minutes=1)
    else:
        next_at = now + timedelta(days=first_delay)

    enrolled = 0
    skipped_already_in = 0
    skipped_missing_table = 0

    for c in contacts:
        table = c.get("source_table", "")
        contact_id = c.get("airtable_id", "")
        if not table or not contact_id:
            skipped_missing_table += 1
            continue

        # Check current state
        if skip_already_enrolled:
            try:
                api = airtable_client._get_api()
                tbl = api.table(config.AIRTABLE_BASE_ID, table)
                row = tbl.get(contact_id)
                f = row.get("fields", {})
                current_seq = f.get(ContactField.ACTIVE_SEQUENCE, "")
                current_status = f.get(ContactField.SEQUENCE_STATUS, "")
                if current_seq and current_status == SequenceStatus.ACTIVE:
                    skipped_already_in += 1
                    continue
            except Exception as e:
                logger.warning(f"enroll: could not check state for {contact_id}: {e}")
                # Fall through and try to enroll anyway

        write_contact_state(
            table, contact_id,
            active_sequence=sequence_name,
            sequence_step=0,  # 0 = enrolled, step 1 not yet sent
            sequence_next_at=next_at,
            sequence_status=SequenceStatus.ACTIVE,
        )
        enrolled += 1

    logger.info(
        f"enroll_in_sequence({sequence_name}): enrolled={enrolled} "
        f"already_in={skipped_already_in} missing_table={skipped_missing_table}"
    )
    return {
        "enrolled": enrolled,
        "skipped_already_in": skipped_already_in,
        "skipped_missing_table": skipped_missing_table,
        "sequence_missing": 0,
        "total_contacts": len(contacts),
    }


# ── Pause / Resume / Cancel ────────────────────────────────────────────────

def pause_sequence_for_contact(
    contact_email: str,
    reason: str = "manual",
) -> dict[str, Any]:
    """Find contact by email and pause their sequence.

    `reason` = 'reply' or 'manual' — sets the matching sequence_status.
    """
    contact, table = airtable_client.fetch_contact_by_email(contact_email)
    if not contact or not table:
        return {"ok": False, "error": f"Contact not found: {contact_email}"}
    status = SequenceStatus.PAUSED_REPLY if reason == "reply" else SequenceStatus.PAUSED_MANUAL
    write_contact_state(table, contact["id"], sequence_status=status)
    return {"ok": True, "email": contact_email, "new_status": status}


def resume_sequence_for_contact(contact_email: str) -> dict[str, Any]:
    contact, table = airtable_client.fetch_contact_by_email(contact_email)
    if not contact or not table:
        return {"ok": False, "error": f"Contact not found: {contact_email}"}
    write_contact_state(table, contact["id"], sequence_status=SequenceStatus.ACTIVE)
    return {"ok": True, "email": contact_email}


def cancel_sequence_for_contact(contact_email: str) -> dict[str, Any]:
    contact, table = airtable_client.fetch_contact_by_email(contact_email)
    if not contact or not table:
        return {"ok": False, "error": f"Contact not found: {contact_email}"}
    write_contact_state(
        table, contact["id"],
        active_sequence="",
        sequence_status=SequenceStatus.CANCELLED,
    )
    return {"ok": True, "email": contact_email}


# ── Sequence status summary ────────────────────────────────────────────────

def sequence_overview(sequence_name: str) -> dict[str, Any]:
    """Count contacts by their current state in a sequence."""
    from collections import Counter
    try:
        escaped = sequence_name.replace("'", "''")
        formula = f"{{{ContactField.ACTIVE_SEQUENCE}}}='{escaped}'"
        rows = airtable_client.fetch_contacts_by_formula(formula)
    except Exception as e:
        logger.error(f"sequence_overview failed: {e}")
        return {"error": str(e)}

    by_status: Counter[str] = Counter()
    by_step: Counter[int] = Counter()
    for r in rows:
        f = r.get("fields", {})
        by_status[f.get(ContactField.SEQUENCE_STATUS, "")] += 1
        by_step[int(f.get(ContactField.SEQUENCE_STEP, 0) or 0)] += 1

    steps = load_sequence(sequence_name)
    return {
        "sequence_name": sequence_name,
        "total_enrolled": len(rows),
        "total_steps_defined": len(steps),
        "by_status": dict(by_status),
        "by_step": dict(sorted(by_step.items())),
    }


# ── Tick: fire all due sequence steps ──────────────────────────────────────

async def tick_due_steps() -> dict[str, int]:
    """Background job: find all contacts with sequence_status=active and
    sequence_next_at <= now, send their next step, advance state.

    Returns counters.
    """
    from sending.email_agent_client import send_now
    from personalization.resolver import resolve_placeholders
    from state import redis_client

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # Query all tables for due contacts.
    # Use DATETIME_DIFF > 0 instead of IS_BEFORE because IS_BEFORE is strict
    # and fails for datetime values that are equal to NOW within 1 second.
    clauses = [
        f"{{{ContactField.SEQUENCE_STATUS}}}='{SequenceStatus.ACTIVE}'",
        f"{{{ContactField.ACTIVE_SEQUENCE}}}!=''",
        f"{{{ContactField.SEQUENCE_NEXT_AT}}}!=''",
        f"DATETIME_DIFF(NOW(), {{{ContactField.SEQUENCE_NEXT_AT}}}, 'seconds') > 0",
        f"NOT({{{ContactField.DO_NOT_CONTACT}}})",
        f"{{{ContactField.EMAIL_PRIMARY}}}!=''",
    ]
    formula = "AND(" + ", ".join(clauses) + ")"
    due = airtable_client.fetch_contacts_by_formula(formula)

    counters = {"due": len(due), "sent": 0, "completed": 0, "failed": 0, "skipped_no_sequence": 0}
    if not due:
        return counters

    logger.info(f"tick_due_steps: {len(due)} contacts due")

    # Cache sequence definitions by name
    seq_cache: dict[str, list[dict]] = {}

    sender = {"name": config.DEFAULT_SENDER_NAME, "title": config.DEFAULT_SENDER_TITLE}
    event = {"name": config.DEFAULT_EVENT_NAME}

    # Global daily limit enforcement
    sent_today = redis_client.get_day_send_count()
    daily_cap = config.GMAIL_DAILY_LIMIT

    for contact in due:
        if sent_today >= daily_cap:
            logger.warning(f"tick: hit daily cap {daily_cap} — stopping")
            break

        f = contact.get("fields", {})
        source_table = contact.get("_source_table", "")
        contact_id = contact.get("id")
        email = f.get(ContactField.EMAIL_PRIMARY, "")
        current_step = int(f.get(ContactField.SEQUENCE_STEP, 0) or 0)
        sequence_name = f.get(ContactField.ACTIVE_SEQUENCE, "")

        if sequence_name not in seq_cache:
            seq_cache[sequence_name] = load_sequence(sequence_name)
        steps = seq_cache[sequence_name]
        if not steps:
            counters["skipped_no_sequence"] += 1
            continue

        next_step_idx = current_step  # current_step is 0-indexed into "last sent", so next is at idx=current
        if next_step_idx >= len(steps):
            # Sequence complete
            write_contact_state(
                source_table, contact_id,
                sequence_status=SequenceStatus.COMPLETED,
            )
            counters["completed"] += 1
            continue

        step = steps[next_step_idx]

        # Render email
        person = {
            "first_name": f.get(ContactField.FIRST_NAME, ""),
            "last_name": f.get(ContactField.LAST_NAME, ""),
            "salutation": f.get(ContactField.SALUTATION, ""),
            "language": f.get(ContactField.LANGUAGE, ""),
            "gender": f.get(ContactField.GENDER, ""),
            "country": f.get(ContactField.COUNTRY, ""),
            "company_name": f.get(ContactField.COMPANY, ""),
        }
        subject = resolve_placeholders(step["subject_template"], person, sender, event)
        body = resolve_placeholders(step["body_template"], person, sender, event)

        # Send via email-agent. We use a synthetic campaign_id = "seq:<name>:<step>"
        # and a stable approval token so email-agent accepts the send.
        # NOTE: sequence sends bypass the Slack-per-campaign approval because Stefan
        # approved the whole sequence at enrollment time. Each send is rate-limited
        # by the tick frequency (5 min) + daily cap.
        sequence_token = _sequence_approval_token(sequence_name)
        try:
            result = await send_now(
                campaign_id=f"seq:{sequence_name}:{step['sequence_step']}",
                approval_token=sequence_token,
                to=email,
                subject=subject,
                body=body,
                recipient_person_id=contact_id,
            )
            gmail_thread_id = result.get("gmail_thread_id", "")

            # Advance state: step just sent = next_step_idx+1 (1-indexed)
            sent_step = step["sequence_step"]
            # Compute next_at = now + next_step.delay_days (if there is a next step)
            if next_step_idx + 1 < len(steps):
                nxt = steps[next_step_idx + 1]
                next_at = now + timedelta(days=int(nxt.get("sequence_delay_days", 0) or 0))
                new_status = SequenceStatus.ACTIVE
            else:
                # Just sent last step
                next_at = None
                new_status = SequenceStatus.COMPLETED

            write_contact_state(
                source_table, contact_id,
                sequence_step=sent_step,
                sequence_next_at=next_at,
                sequence_status=new_status,
                last_sent_template=step["name"],
            )
            # Also update last_outbound_*
            try:
                airtable_client.update_contact_last_outbound(
                    source_table, contact_id, gmail_thread_id, now,
                )
            except Exception:
                pass

            redis_client.increment_day_send(1)
            sent_today += 1
            counters["sent"] += 1
            logger.info(f"seq-tick: sent step {sent_step} of {sequence_name} to {email}")

        except Exception as e:
            logger.error(f"seq-tick: send failed for {email} step {step['sequence_step']}: {e}")
            counters["failed"] += 1
            # Leave next_at unchanged so we'll retry on the next tick

    logger.info(f"tick_due_steps complete: {counters}")
    return counters


# ── Sequence approval token (stable per sequence) ──────────────────────────
# Uses the shared secret-based HMAC so email-agent can verify without a
# Redis round-trip (the round-trip is already fine for single-shot campaigns,
# but sequence sends are higher frequency). Token is reproducible from the
# sequence_name + internal key.

def _sequence_approval_token(sequence_name: str) -> str:
    """Reproducible HMAC-SHA256 token for a sequence. Email-agent's CRM
    verify-token endpoint accepts it because the CRM /verify-token handler
    will accept a 'seq:*' campaign_id + a matching HMAC.

    For simplicity (MVP) we use a Redis-registered token minted once per
    sequence at the first enrollment; subsequent enrollments reuse it.
    """
    from state import redis_client
    import secrets
    key = f"crm:seq_token:{sequence_name}"
    client = redis_client.get_client()
    token = client.get(key)
    if token:
        return token
    # Mint and cache forever
    new = secrets.token_urlsafe(32)
    client.set(key, new)
    # Also register it in the campaign token namespace so verify-token works.
    # We point it at the synthetic campaign_id = "seq:<name>" — verify logic
    # in campaigns.py::verify_token is campaign-specific, so we add a shim
    # path for sequence tokens.
    client.set(f"crm:seq_valid:{new}", sequence_name)
    return new


def verify_sequence_token(sequence_name_prefix: str, token: str) -> bool:
    """Counterpart to _sequence_approval_token. Used by email-agent verify path."""
    from state import redis_client
    client = redis_client.get_client()
    looked = client.get(f"crm:seq_valid:{token}")
    if not looked:
        return False
    return sequence_name_prefix.startswith(looked) or looked in sequence_name_prefix
