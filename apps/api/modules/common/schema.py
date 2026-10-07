"""OpenAPI schema extensions (SVX-TECH-001 section 6.1).

The schema is the contract the frontend generates its TypeScript types from, so
an endpoint drf-spectacular cannot describe becomes an endpoint the client
calls untyped. That is why CI validates the schema and fails on warnings.

This module teaches the generator two things it cannot infer: how the custom
session authenticator presents itself, and the shape of the error envelope every
endpoint can return.
"""

from __future__ import annotations

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework import serializers


class CsrfSessionAuthenticationExtension(OpenApiAuthenticationExtension):
    """Describe the cookie-session scheme used on the shared origin."""

    target_class = "modules.common.authentication.CsrfSessionAuthentication"
    name = "sessionAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "cookie",
            "name": "sessionid",
            "description": (
                "Server-side session cookie set by /accounts/login/. Unsafe "
                "methods must also echo the csrftoken cookie in the "
                "X-CSRFToken header."
            ),
        }


class ErrorEnvelopeSerializer(serializers.Serializer):
    """The uniform error body documented in section 6.2.

    Declared so that clients can generate a type for it rather than
    reverse-engineering it from a failing response.
    """

    code = serializers.CharField(help_text="Stable machine-readable code, e.g. version_conflict.")
    message = serializers.CharField(help_text="Human-readable explanation.")
    field_errors = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        help_text="Per-field validation messages, keyed by field name.",
    )
    request_id = serializers.CharField(
        help_text="Correlates this response with the server log entry."
    )
