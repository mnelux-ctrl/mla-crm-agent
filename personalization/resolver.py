"""
personalization/resolver.py — Pure placeholder resolution.

Master rule: if PERSON.salutation is set in Airtable, it overrides everything.
Stefan fills these manually (e.g. "Draga Marijana", "Poštovani g-dine Petroviću")
so he controls tone, gender, formality per-person. That's the "mnoogo pametan"
behaviour: respect the human's explicit intent.

Fallback hierarchy when salutation is empty:
  1. Detect language (mne vs en) from PERSON.language or PERSON.country.
  2. If first_name known: "Poštovani {name}" / "Dear {name}".
  3. Else: "Poštovani" / "Hello" (no name at all).

Unknown placeholders are kept literal in the output — visible in the Slack preview
as `{{foo}}`, so Stefan sees the gap before approving.
"""

from __future__ import annotations

import re
from typing import Any

PLACEHOLDER_RE = re.compile(r"\{\{([a-z_]+)\}\}")

# Supported placeholder names — anything else is left literal for visibility.
KNOWN_PLACEHOLDERS = frozenset({
    "salutation",
    "first_name",
    "last_name",
    "full_name",
    "company_name",
    "sender_name",
    "sender_title",
    "event_name",
})


def resolve_language(person: dict[str, Any]) -> str:
    """Return 'mne' or 'en'. Conservative: defaults to English unless MNE/SRB signal."""
    lang = (person.get("language") or "").strip().lower()
    country = (person.get("country") or "").strip().lower()
    if "mont" in lang or "srb" in lang or "serb" in lang or "crnogor" in lang:
        return "mne"
    if "montenegro" in country or "crna gora" in country or "serbia" in country or "srbija" in country:
        return "mne"
    return "en"


def _fallback_salutation(person: dict[str, Any]) -> str:
    """Auto-compose a conservative formal greeting when PERSON.salutation is empty."""
    lang = resolve_language(person)
    first_name = (person.get("first_name") or "").strip()
    if lang == "mne":
        return f"Poštovani {first_name}".strip() if first_name else "Poštovani"
    return f"Dear {first_name}".strip() if first_name else "Hello"


def build_value_map(
    person: dict[str, Any],
    sender: dict[str, Any] | None = None,
    event: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Build the {placeholder_name: rendered_value} mapping for one recipient."""
    sender = sender or {}
    event = event or {}

    salutation = (person.get("salutation") or "").strip()
    if not salutation:
        salutation = _fallback_salutation(person)

    first_name = (person.get("first_name") or "").strip()
    last_name = (person.get("last_name") or "").strip()
    full_name = f"{first_name} {last_name}".strip()

    return {
        "salutation":   salutation,
        "first_name":   first_name,
        "last_name":    last_name,
        "full_name":    full_name,
        "company_name": (person.get("company_name") or "").strip(),
        "sender_name":  (sender.get("name") or "").strip(),
        "sender_title": (sender.get("title") or "").strip(),
        "event_name":   (event.get("name") or "").strip(),
    }


def resolve_placeholders(
    body: str,
    person: dict[str, Any],
    sender: dict[str, Any] | None = None,
    event: dict[str, Any] | None = None,
) -> str:
    """Substitute all known {{placeholders}} in `body` for one recipient.

    Unknown placeholders are left literal (visible as `{{foo}}` so Stefan spots gaps
    in preview). Empty values substitute as empty string — the greeting fallback
    inside build_value_map handles the most important case (salutation).
    """
    values = build_value_map(person, sender, event)

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in KNOWN_PLACEHOLDERS:
            return match.group(0)  # keep literal
        return values.get(key, "")

    return PLACEHOLDER_RE.sub(_replace, body)


def list_placeholders(body: str) -> list[str]:
    """Return all {{placeholder}} names found in `body`, preserving order, unique."""
    seen: list[str] = []
    for match in PLACEHOLDER_RE.finditer(body):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def find_unknown_placeholders(body: str) -> list[str]:
    """Placeholder names that appear in body but aren't in KNOWN_PLACEHOLDERS."""
    return [p for p in list_placeholders(body) if p not in KNOWN_PLACEHOLDERS]


def find_unresolved_for_recipient(
    body: str, person: dict[str, Any]
) -> list[str]:
    """Placeholders that are KNOWN but would render empty for THIS person.

    Used by the preview/outlier detector to flag recipients with data gaps.
    salutation is never flagged here because build_value_map always provides a fallback.
    """
    values = build_value_map(person)
    gaps: list[str] = []
    for name in list_placeholders(body):
        if name not in KNOWN_PLACEHOLDERS:
            continue
        if not values.get(name, "").strip():
            gaps.append(name)
    return gaps
