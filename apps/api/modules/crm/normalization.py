"""Contact normalisation and duplicate matching (CRM02).

Two rules from the brief drive everything here:

* Normalise "without guessing country or stripping meaningful email
  characters". A local part may legitimately contain ``+``, ``.`` and unusual
  characters; removing them merges distinct people.
* Duplicate matching prefers an exact normalised email or a *valid* normalised
  phone. Similar names are suggestions only, never automatic merges.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import phonenumbers
from phonenumbers import NumberParseException

WHITESPACE = re.compile(r"\s+")


def normalize_email(raw: str) -> str:
    """Lower-case and trim. Nothing else.

    Deliberately does *not* strip dots or ``+tag`` suffixes. Gmail happens to
    treat those as equivalent; most providers do not, and applying Gmail's rule
    universally would merge unrelated mailboxes.
    """
    value = (raw or "").strip().lower()
    if not value or value.count("@") != 1:
        return ""
    local, _, domain = value.partition("@")
    if not local or not domain or "." not in domain:
        return ""
    return f"{local}@{domain}"


def normalize_phone(raw: str, *, default_country: str | None = None) -> str:
    """Return E.164, or empty when the number cannot be parsed confidently.

    A national number without country context is *not* guessed. An empty result
    means "do not use this for matching", which is safer than inventing a
    country code and merging two different people who share the last nine
    digits.
    """
    value = (raw or "").strip()
    if not value:
        return ""

    region = (default_country or "").strip().upper() or None
    if not value.startswith("+") and region is None:
        return ""

    try:
        parsed = phonenumbers.parse(value, region)
    except NumberParseException:
        return ""

    if not phonenumbers.is_valid_number(parsed):
        return ""
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def normalize_name(raw: str) -> str:
    """Collapse whitespace and case for suggestion-only name comparison."""
    return WHITESPACE.sub(" ", (raw or "").strip()).lower()


# Characters that make a spreadsheet application evaluate a cell as a formula.
# An exported cell beginning with one of these is prefixed on export (CRM02).
FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def formula_safe(value: str) -> str:
    """Neutralise a CSV value that a spreadsheet would execute as a formula.

    ``=cmd|'/c calc'!A1`` in a name column becomes a command execution the
    moment somebody opens the export. Prefixing with an apostrophe makes the
    cell display as text.
    """
    text = "" if value is None else str(value)
    if text.startswith(FORMULA_TRIGGERS):
        return "'" + text
    return text


@dataclass(slots=True)
class DuplicateMatch:
    """A candidate duplicate and how confident the match is."""

    contact_id: object
    reason: str
    #: True for an exact email or valid phone match; False for a name
    #: similarity, which is a suggestion requiring human review.
    is_definitive: bool


def find_duplicates(
    *, workspace_id, normalized_email: str, phone_e164: str, display_name: str
) -> list[DuplicateMatch]:
    """Find existing contacts that may be the same person.

    Definitive matches (email, valid phone) can drive an automatic skip during
    import. Name matches are returned for review and must never merge on their
    own.
    """
    from modules.crm.models import Contact

    matches: list[DuplicateMatch] = []

    if normalized_email:
        for contact_id in Contact.objects.filter(
            workspace_id=workspace_id,
            normalized_email=normalized_email,
            deleted_at__isnull=True,
        ).values_list("id", flat=True):
            matches.append(DuplicateMatch(contact_id, "Exact email match", is_definitive=True))

    if phone_e164:
        for contact_id in Contact.objects.filter(
            workspace_id=workspace_id, phone_e164=phone_e164, deleted_at__isnull=True
        ).values_list("id", flat=True):
            if any(m.contact_id == contact_id for m in matches):
                continue
            matches.append(DuplicateMatch(contact_id, "Exact phone match", is_definitive=True))

    normalized = normalize_name(display_name)
    if normalized and not matches:
        for contact_id, name in Contact.objects.filter(
            workspace_id=workspace_id, deleted_at__isnull=True
        ).values_list("id", "display_name")[:5000]:
            if normalize_name(name) == normalized:
                matches.append(
                    DuplicateMatch(
                        contact_id, "Similar name (review required)", is_definitive=False
                    )
                )

    return matches
