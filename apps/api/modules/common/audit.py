"""Audit writing and redaction (SVX-TECH-001 section 5.4)."""

from __future__ import annotations

import uuid
from typing import Any

from django.db import models

from modules.common.models import AuditAction, AuditEvent
from modules.common.tenancy import current_workspace_id

# Field names whose values never reach an audit record, a log line or an AI
# prompt. Matching is on a normalised substring so that `oauth_refresh_token`
# and `refreshToken` are both caught.
SENSITIVE_FIELD_MARKERS = (
    "password",
    "token",
    "secret",
    "credential",
    "api_key",
    "apikey",
    "private_key",
    "session",
    "csrf",
    "recovery_code",
    "totp",
)

REDACTED = "[redacted]"


def _is_sensitive(field_name: str) -> bool:
    normalised = field_name.lower().replace("-", "_")
    return any(marker in normalised for marker in SENSITIVE_FIELD_MARKERS)


def redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Replace sensitive values, recursing into nested structures."""
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if _is_sensitive(key):
            clean[key] = REDACTED
        elif isinstance(value, dict):
            clean[key] = redact(value)
        elif isinstance(value, list):
            clean[key] = [redact(v) if isinstance(v, dict) else _coerce(v) for v in value]
        else:
            clean[key] = _coerce(value)
    return clean


def _coerce(value: Any) -> Any:
    """Make a value JSON-storable without losing its meaning."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def diff_fields(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    """Build a redacted before/after snapshot of changed fields only."""
    before = before or {}
    after = after or {}
    changed = {}
    for key in set(before) | set(after):
        old, new = before.get(key), after.get(key)
        if old != new:
            changed[key] = {"from": _coerce(old), "to": _coerce(new)}
    return redact(changed)


def snapshot(instance: models.Model, fields: list[str]) -> dict[str, Any]:
    """Capture named fields from a model instance for diffing."""
    return {field: getattr(instance, field, None) for field in fields}


def record_audit(
    *,
    action: str | AuditAction,
    entity_type: str,
    entity_id: uuid.UUID | None,
    actor=None,
    changes: dict[str, Any] | None = None,
    reason: str = "",
    request_id: str = "",
    correlation_id: uuid.UUID | None = None,
    corrects: AuditEvent | None = None,
    workspace_id: uuid.UUID | None = None,
) -> AuditEvent:
    """Append one audit event.

    Must be called inside the same transaction as the change it describes, so
    that a rolled-back change leaves no audit trace claiming it happened
    (section 3.2).
    """
    return AuditEvent.objects.create(
        workspace_id=workspace_id or current_workspace_id(),
        actor=actor,
        actor_label=getattr(actor, "email", "") or "",
        action=str(action),
        entity_type=entity_type,
        entity_id=entity_id,
        changes=redact(changes or {}),
        reason=reason,
        request_id=request_id,
        correlation_id=correlation_id,
        corrects=corrects,
    )
