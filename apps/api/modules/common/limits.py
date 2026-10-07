"""Input limits and typed fields shared by every API serializer.

One place for how long each kind of free text may be, so the API, the
database and the interface agree (the web app mirrors these numbers in
``apps/web/src/lib/limits.ts``). Every writable text field has a ceiling: an
unbounded field lets one request store megabytes, slows every list that shows
it, and turns a typo into an outage.

The typed fields below normalise and validate structured values (currency and
country codes, phone numbers, money) at the edge, so services receive clean
data and a bad value is refused with a message saying what is expected.
"""

from __future__ import annotations

import re
from decimal import Decimal

from django.core.validators import RegexValidator
from rest_framework import serializers

# Character limits, from shortest to longest.
CURRENCY = 3
COUNTRY = 2
PHONE = 40
JOB_TITLE = 150
NAME = 200
EVIDENCE_REFERENCE = 200
EMAIL = 254  # Django's EmailField column width (RFC 5321 path limit).
TITLE = 300
REFERENCE = 500
SHORT_REASON = 1000
REASON = 2000
LONG_TEXT = 5000
COMMENT = 10000
PASSWORD = 256

# Money: 18 digits with 2 decimal places, matching the database columns.
MONEY_MAX_DIGITS = 18
MONEY_DECIMAL_PLACES = 2

# Digits, spaces and the punctuation people write phone numbers with, plus an
# optional extension ("x 204", "ext. 204"). Letters are refused: they are
# almost always a value pasted into the wrong field.
PHONE_PATTERN = re.compile(
    r"^\+?[0-9()\-.\s/]{3,}(?:\s*(?:x|ext\.?)\s*[0-9]{1,6})?$", re.IGNORECASE
)


def text(limit: int, *, required: bool = True, **kwargs) -> serializers.CharField:
    """Free text with a ceiling.

    Required text may not be blank or whitespace-only (DRF trims before the
    blank check). Optional text may be omitted or blank.
    """
    if required:
        return serializers.CharField(max_length=limit, **kwargs)
    return serializers.CharField(max_length=limit, required=False, allow_blank=True, **kwargs)


class UpperCodeField(serializers.CharField):
    """A fixed-length alphabetic code (ISO 4217 currency, ISO 3166 country),
    accepted in any case and stored upper-case."""

    def __init__(self, length: int, label: str, **kwargs):
        self.length = length
        kwargs.setdefault("max_length", length)
        super().__init__(**kwargs)
        self.validators.append(
            RegexValidator(
                rf"^[A-Z]{{{length}}}$",
                message=f"Use a {length}-letter {label} code, for example "
                f"{'USD' if length == 3 else 'PK'}.",
            )
        )

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        return value.upper()


def currency(*, required: bool = True) -> UpperCodeField:
    if required:
        return UpperCodeField(CURRENCY, "currency")
    return UpperCodeField(CURRENCY, "currency", required=False, allow_blank=True)


def country(*, required: bool = False) -> UpperCodeField:
    if required:
        return UpperCodeField(COUNTRY, "country")
    return UpperCodeField(COUNTRY, "country", required=False, allow_blank=True)


def phone(*, required: bool = False) -> serializers.CharField:
    # Validators go in through the constructor: DRF deep-copies declared
    # fields by re-running __init__, so one appended afterwards is lost.
    return serializers.CharField(
        max_length=PHONE,
        required=required,
        allow_blank=not required,
        validators=[
            RegexValidator(
                PHONE_PATTERN,
                message="Use digits, spaces, +, -, ( ) and an optional extension, "
                "for example +92 300 1234567.",
            )
        ],
    )


def email(*, required: bool = True) -> serializers.EmailField:
    if required:
        return serializers.EmailField(max_length=EMAIL)
    return serializers.EmailField(max_length=EMAIL, required=False, allow_blank=True)


def money(*, minimum: Decimal | None = Decimal("0"), **kwargs) -> serializers.DecimalField:
    """A money amount. Zero or more unless ``minimum`` says otherwise; pass
    ``minimum=None`` where a signed amount is legitimate (a correction)."""
    if minimum is not None:
        kwargs.setdefault("min_value", minimum)
    return serializers.DecimalField(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, **kwargs
    )
