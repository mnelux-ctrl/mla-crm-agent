"""
End-to-end rendering tests — NO network.

Simulate the render-to-preview path:
  raw_recipients → resolve_placeholders → rendered messages →
  detect warnings → mimic the /api/crm/campaigns POST output shape.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personalization.resolver import (
    resolve_placeholders,
    find_unresolved_for_recipient,
)
from sending.scheduler import compute_send_schedule


# The exact body Stefan might write
MASTER_SUBJECT = "{{salutation}}, partnership opportunity — {{event_name}}"
MASTER_BODY = """{{salutation}},

pišem vam povodom {{event_name}} — luxury travel summita koji MLA
organizuje u Crnoj Gori, 31.03–04.04.2026.

{{company_name}} bi bio savršen partner za naš boutique trade show.
Voleo bih da zakažemo kratak poziv kako biste čuli više detalja.

Srdačan pozdrav,
{{sender_name}}
{{sender_title}}"""

SENDER = {"name": "Stefan Stešević", "title": "Founder & Executive Director"}
EVENT = {"name": "LTSM 2026"}


def test_three_real_recipients_render_differently():
    """Simulates what Stefan sees in Slack preview: 3 distinct rendered bodies."""
    recipients = [
        {   # Leonarda: female MNE, salutation set manually
            "airtable_id": "rec001",
            "first_name": "Leonarda", "last_name": "Dedić",
            "salutation": "Draga Leonarda", "language": "Montenegrin",
            "company_name": "The Chedi Luštica Bay",
            "email": "leonarda@chedilusticabay.me",
        },
        {   # Vladimir: male MNE, salutation NOT set — falls back
            "airtable_id": "rec002",
            "first_name": "Vladimir", "last_name": "Vukmanović",
            "salutation": "", "language": "Montenegrin",
            "company_name": "Regent Porto Montenegro",
            "email": "vladimir@regentportomontenegro.com",
        },
        {   # Isabel: English, salutation manually polished
            "airtable_id": "rec003",
            "first_name": "Isabel", "last_name": "García",
            "salutation": "Dear Isabel", "language": "English",
            "company_name": "One&Only Portonovi",
            "email": "isabel@oneandonly.com",
        },
    ]

    rendered = []
    for r in recipients:
        subject = resolve_placeholders(MASTER_SUBJECT, r, SENDER, EVENT)
        body = resolve_placeholders(MASTER_BODY, r, SENDER, EVENT)
        rendered.append({"email": r["email"], "subject": subject, "body": body})

    # Distinct renderings
    bodies = [r["body"] for r in rendered]
    assert len(set(bodies)) == 3

    # Subject sanity
    assert rendered[0]["subject"] == "Draga Leonarda, partnership opportunity — LTSM 2026"
    assert rendered[1]["subject"] == "Poštovani Vladimir, partnership opportunity — LTSM 2026"
    assert rendered[2]["subject"] == "Dear Isabel, partnership opportunity — LTSM 2026"

    # Body contains company name for each
    assert "Chedi Luštica Bay" in rendered[0]["body"]
    assert "Regent Porto Montenegro" in rendered[1]["body"]
    assert "One&Only Portonovi" in rendered[2]["body"]

    # Sender signature present
    for r in rendered:
        assert "Stefan Stešević" in r["body"]
        assert "Founder & Executive Director" in r["body"]


def test_warnings_emitted_for_missing_company():
    person = {"first_name": "Marko", "language": "Montenegrin", "company_name": ""}
    gaps = find_unresolved_for_recipient(MASTER_BODY, person)
    assert "company_name" in gaps


def test_full_campaign_shape_42_recipients():
    """Simulates a 42-recipient campaign: rendered bodies + schedule."""
    from random import Random

    recipients = []
    for i in range(42):
        recipients.append({
            "airtable_id": f"rec{i:03d}",
            "first_name": f"Name{i}",
            "last_name": "Surname",
            "salutation": f"Poštovani Name{i}" if i % 2 == 0 else "",
            "language": "Montenegrin" if i % 3 != 0 else "English",
            "company_name": f"Company {i}",
            "email": f"rec{i}@test.com",
        })

    # Render
    rendered = []
    for r in recipients:
        body = resolve_placeholders(MASTER_BODY, r, SENDER, EVENT)
        assert "{{" not in body or body.count("{{") == body.count("}}")  # no stray open braces
        rendered.append({"email": r["email"], "body": body})

    # Schedule
    start = datetime(2026, 4, 17, 10, 0, 0, tzinfo=timezone.utc)
    schedule = compute_send_schedule(42, start, rng=Random(42))
    assert len(schedule) == 42
    # Total duration: 42 messages × ~270s base ± jitter = ~3h
    total_seconds = (schedule[-1] - schedule[0]).total_seconds()
    assert 7000 < total_seconds < 14000  # ~2h to ~4h with jitter

    # No duplicate fire times
    assert len(set(schedule)) == 42


def test_empty_placeholder_values_dont_break_body():
    """Missing data should render as empty string, not crash."""
    sparse = {"first_name": "", "language": "", "company_name": ""}
    body = resolve_placeholders(MASTER_BODY, sparse, SENDER, EVENT)
    # Salutation falls back to "Hello"
    assert "Hello" in body
    # {{company_name}} renders empty — no NameError
    assert "{{company_name}}" not in body
