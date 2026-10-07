"""TEST11 - audit history and redaction; section 6.2 - idempotency."""

from __future__ import annotations

import pytest

from modules.common.audit import REDACTED, diff_fields, redact
from modules.common.exceptions import ConflictError
from modules.common.idempotency import complete, hash_payload, reserve
from modules.common.tenancy import workspace_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_sensitive_fields_are_redacted():
    """Audit records and logs must never carry credentials (section 5.4)."""
    payload = {
        "email": "ayesha@example.test",
        "password": "hunter2",
        "oauth_refresh_token": "abc",
        "api_key": "sk-live-123",
        "nested": {"client_secret": "shh", "name": "Acme"},
    }
    clean = redact(payload)

    assert clean["email"] == "ayesha@example.test"
    assert clean["password"] == REDACTED
    assert clean["oauth_refresh_token"] == REDACTED
    assert clean["api_key"] == REDACTED
    assert clean["nested"]["client_secret"] == REDACTED
    assert clean["nested"]["name"] == "Acme"


def test_redaction_matches_camel_case_and_hyphens():
    clean = redact({"refreshToken": "x", "api-key": "y"})
    assert clean["refreshToken"] == REDACTED
    assert clean["api-key"] == REDACTED


def test_diff_records_only_changed_fields():
    changes = diff_fields(
        {"stage": "discovery", "owner": "a"}, {"stage": "qualified", "owner": "a"}
    )
    assert set(changes) == {"stage"}
    assert changes["stage"] == {"from": "discovery", "to": "qualified"}


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_replaying_the_same_request_returns_the_stored_response(workspace, sales_manager):
    payload = {"preview_hash": "abc", "expected_version": 1}
    with workspace_context(workspace.id):
        first = reserve(actor=sales_manager.user, route="imports/commit", key="k1", payload=payload)
        assert first.replayed is False
        complete(first.record, status=202, body={"id": "batch-1"})

        second = reserve(
            actor=sales_manager.user, route="imports/commit", key="k1", payload=payload
        )
    assert second.replayed is True
    assert second.stored_response == (202, {"id": "batch-1"})


def test_reusing_a_key_for_a_different_payload_is_a_conflict(workspace, sales_manager):
    """Treating it as the original request would hide a real client bug."""
    with workspace_context(workspace.id):
        first = reserve(
            actor=sales_manager.user,
            route="imports/commit",
            key="k2",
            payload={"a": 1},
        )
        complete(first.record, status=202, body={})

        with pytest.raises(ConflictError) as exc:
            reserve(
                actor=sales_manager.user,
                route="imports/commit",
                key="k2",
                payload={"a": 2},
            )
    assert exc.value.code == "idempotency_key_reused"


def test_an_in_flight_duplicate_is_reported_rather_than_run_twice(workspace, sales_manager):
    with workspace_context(workspace.id):
        reserve(
            actor=sales_manager.user,
            route="imports/commit",
            key="k3",
            payload={"a": 1},
        )
        with pytest.raises(ConflictError) as exc:
            reserve(
                actor=sales_manager.user,
                route="imports/commit",
                key="k3",
                payload={"a": 1},
            )
    assert exc.value.code == "idempotent_request_in_flight"


def test_payload_hash_ignores_key_order():
    assert hash_payload({"a": 1, "b": 2}) == hash_payload({"b": 2, "a": 1})


def test_keys_are_scoped_per_actor(workspace, sales_manager, sales_rep):
    """One user's key must not collide with another's."""
    with workspace_context(workspace.id):
        reserve(actor=sales_manager.user, route="r", key="shared", payload={"a": 1})
        other = reserve(actor=sales_rep.user, route="r", key="shared", payload={"a": 1})
    assert other.replayed is False
