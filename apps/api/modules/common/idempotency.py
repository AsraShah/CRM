"""Idempotency keys for unsafe operations (SVX-TECH-001 section 6.2)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from modules.common.exceptions import ConflictError
from modules.common.models import IdempotencyRecord
from modules.common.tenancy import current_workspace_id

IDEMPOTENCY_HEADER = "Idempotency-Key"


def hash_payload(payload: Any) -> str:
    """Stable hash of a request body, independent of key ordering."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class IdempotencyOutcome:
    """Either a replay of a completed request, or a fresh reservation."""

    record: IdempotencyRecord
    replayed: bool

    @property
    def stored_response(self) -> tuple[int, Any] | None:
        if self.replayed and self.record.response_status is not None:
            return self.record.response_status, self.record.response_body
        return None


def reserve(*, actor, route: str, key: str, payload: Any) -> IdempotencyOutcome:
    """Claim an idempotency key, or detect a replay.

    Three cases:

    * key unseen           -> reserve it, caller proceeds
    * key seen, same hash  -> replay; return the stored response if the original
                              finished, otherwise report the in-flight conflict
    * key seen, other hash -> 409. The client reused a key for a different
                              request, which is a client bug we must not paper
                              over.
    """
    request_hash = hash_payload(payload)
    workspace_id = current_workspace_id()

    try:
        with transaction.atomic():
            record = IdempotencyRecord.objects.create(
                workspace_id=workspace_id,
                actor=actor,
                route=route,
                key=key,
                request_hash=request_hash,
            )
        return IdempotencyOutcome(record=record, replayed=False)
    except IntegrityError:
        pass

    record = IdempotencyRecord.objects.select_for_update().get(
        workspace_id=workspace_id, actor=actor, route=route, key=key
    )
    if record.request_hash != request_hash:
        raise ConflictError(
            "This idempotency key was already used for a different request body.",
            code="idempotency_key_reused",
        )
    if record.completed_at is None:
        raise ConflictError(
            "An identical request is still in progress.",
            code="idempotent_request_in_flight",
        )
    return IdempotencyOutcome(record=record, replayed=True)


def json_safe(value: Any) -> Any:
    """Coerce a serialized response into something a JSONField can store.

    DRF renders a UUID primary-key *relation* as the ``UUID`` object itself,
    not a string, so a response body that looks like plain JSON can still carry
    values ``json.dumps`` refuses. Round-tripping with ``default=str`` converts
    those — and Decimals, dates and enums — exactly as the HTTP renderer would.
    """
    return json.loads(json.dumps(value, default=str))


def complete(record: IdempotencyRecord, *, status: int, body: Any) -> None:
    """Store the response so a later replay returns the same result."""
    record.response_status = status
    record.response_body = json_safe(body)
    record.completed_at = timezone.now()
    record.save(update_fields=["response_status", "response_body", "completed_at"])
