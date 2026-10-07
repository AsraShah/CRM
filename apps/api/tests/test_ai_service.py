"""CRM12 / RD08 — the AI drafting path, with the provider mocked.

This is the module where a defect is most expensive: cross-client disclosure,
unbudgeted spend, or a fabricated commercial fact presented as grounded. None
of that is reachable without executing the code, so the provider call is mocked
at the adapter boundary and everything around it runs for real.

The prompt-injection case is the important one. Notes routinely contain
imperative text — a forwarded email, a customer instruction. The test asserts
the defence that actually holds: whatever the model returns, the output schema
has no field capable of expressing an action, and every citation is checked
against what was really sent.
"""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
from django.utils import timezone

from modules.ai import budget, services
from modules.ai.models import AIDraft, DraftPurpose, ReservationState
from modules.common.exceptions import BudgetExceeded, ValidationFailed
from modules.common.tenancy import workspace_context
from modules.crm import services as crm_services
from modules.crm.models import ActivityKind

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def ai_enabled(settings):
    settings.FEATURE_AI_ENABLED = True
    settings.AI_MONTHLY_CEILING_USD = "2.00"
    settings.AI_MAX_CONCURRENT_REQUESTS = 1
    settings.OPENAI_API_KEY = "test-key-not-real"
    return settings


@pytest.fixture
def notes(workspace, sales_rep, contact_factory):
    """Two activities on one contact, which is what a summary is built from."""
    with workspace_context(workspace.id):
        contact = contact_factory(workspace, display_name="Acme Ltd")
        first = crm_services.record_activity(
            membership=sales_rep,
            actor=sales_rep.user,
            contact=contact,
            kind=ActivityKind.CALL,
            occurred_at=timezone.now() - timedelta(days=2),
            outcome="Discussed the rebuild. They want a quote by Friday.",
        )
        second = crm_services.record_activity(
            membership=sales_rep,
            actor=sales_rep.user,
            contact=contact,
            kind=ActivityKind.NOTE,
            occurred_at=timezone.now() - timedelta(days=1),
            outcome="Budget is not confirmed yet.",
        )
        yield contact, [first, second]


def _mock_provider(monkeypatch, payload: dict, *, usage: dict | None = None):
    """Make the adapter return one canned response without any network call."""
    body = {
        "output_text": json.dumps(payload),
        "usage": usage or {"input_tokens": 400, "output_tokens": 120},
    }

    class _Response:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            return body

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr(httpx, "Client", _Client)


def _raise_on_send(monkeypatch, exc: Exception):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            raise exc

    monkeypatch.setattr(httpx, "Client", _Client)


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_summary_is_stored_with_its_sources(workspace, sales_rep, notes, monkeypatch):
    contact, activities = notes
    _mock_provider(
        monkeypatch,
        {
            "summary": "Acme want a quote by Friday; budget unconfirmed.",
            "suggested_next_action": "Send the quote.",
            "source_ids": [str(a.id) for a in activities],
            "uncertainties": ["Budget is not confirmed."],
        },
    )

    with workspace_context(workspace.id):
        draft = services.create_draft(
            membership=sales_rep,
            actor=sales_rep.user,
            purpose=DraftPurpose.SUMMARISE_NOTES,
            activity_ids=[a.id for a in activities],
        )

    assert draft.output["summary"].startswith("Acme want a quote")
    # The source view must be reproducible for human comparison.
    assert set(draft.source_ids) == {str(a.id) for a in activities}
    assert draft.uncertainties == ["Budget is not confirmed."]
    assert draft.validation_error == ""


def test_actual_usage_is_settled_against_the_budget(workspace, sales_rep, notes, monkeypatch):
    contact, activities = notes
    _mock_provider(
        monkeypatch,
        {
            "summary": "Short summary.",
            "source_ids": [str(activities[0].id)],
            "uncertainties": [],
        },
        usage={"input_tokens": 300, "output_tokens": 90},
    )

    with workspace_context(workspace.id):
        draft = services.create_draft(
            membership=sales_rep,
            actor=sales_rep.user,
            purpose=DraftPurpose.SUMMARISE_NOTES,
            activity_ids=[activities[0].id],
        )
        reservation = draft.reservation
        reservation.refresh_from_db()
        period = budget.current_period()

    assert reservation.state == ReservationState.SETTLED
    assert reservation.actual_input_tokens == 300
    # The reservation is released and replaced by the real cost.
    assert period.reserved_usd == 0
    assert period.settled_usd > 0


# ---------------------------------------------------------------------------
# Authorisation of inputs
# ---------------------------------------------------------------------------


def test_notes_from_two_contacts_are_refused(
    workspace, sales_rep, notes, contact_factory, monkeypatch
):
    """Mixing clients in one prompt is how cross-client disclosure happens."""
    contact, activities = notes
    with workspace_context(workspace.id):
        other = contact_factory(workspace, display_name="Different client")
        stray = crm_services.record_activity(
            membership=sales_rep,
            actor=sales_rep.user,
            contact=other,
            kind=ActivityKind.NOTE,
            occurred_at=timezone.now() - timedelta(hours=3),
            outcome="Unrelated.",
        )

        with pytest.raises(ValidationFailed) as exc:
            services.create_draft(
                membership=sales_rep,
                actor=sales_rep.user,
                purpose=DraftPurpose.SUMMARISE_NOTES,
                activity_ids=[activities[0].id, stray.id],
            )
    assert exc.value.code == "mixed_contacts"


def test_a_representative_cannot_summarise_a_colleagues_notes(
    workspace, sales_rep, other_rep, notes
):
    """Identifiers supplied by the client are filtered, not trusted."""
    contact, activities = notes
    with workspace_context(workspace.id), pytest.raises(ValidationFailed) as exc:
        services.create_draft(
            membership=other_rep,
            actor=other_rep.user,
            purpose=DraftPurpose.SUMMARISE_NOTES,
            activity_ids=[activities[0].id],
        )
    assert exc.value.code == "unauthorised_sources"


# ---------------------------------------------------------------------------
# Output validation — RD08
# ---------------------------------------------------------------------------


def test_output_citing_an_unsupplied_source_is_rejected(workspace, sales_rep, notes, monkeypatch):
    """A citation we never sent means the output cannot be trusted at all."""
    import uuid

    contact, activities = notes
    _mock_provider(
        monkeypatch,
        {
            "summary": "Includes a source that was never supplied.",
            "source_ids": [str(activities[0].id), str(uuid.uuid4())],
            "uncertainties": [],
        },
    )

    # Called the way the view calls it: not inside an outer transaction, so the
    # failure record survives the exception.
    with pytest.raises(ValidationFailed) as exc:
        services.create_draft(
            membership=sales_rep,
            actor=sales_rep.user,
            purpose=DraftPurpose.SUMMARISE_NOTES,
            activity_ids=[activities[0].id],
            workspace_id=workspace.id,
        )

    with workspace_context(workspace.id):
        # The rejection is retained: RD08 needs the failures, not just passes.
        rejected = AIDraft.objects.latest("created_at")

    assert exc.value.code == "ai_output_rejected"
    assert rejected.output == {}
    assert "not supplied" in rejected.validation_error


def test_an_action_smuggled_into_the_output_cannot_survive_the_schema(
    workspace, sales_rep, notes, monkeypatch
):
    """Defence in depth against instructions hidden in notes.

    Even if a note persuaded the model to try, there is no field in the schema
    that can express "send this", so the response fails validation outright.
    """
    contact, activities = notes
    _mock_provider(
        monkeypatch,
        {
            "summary": "Ignoring the instruction in the notes.",
            "source_ids": [str(activities[0].id)],
            "uncertainties": [],
            "send_to": "client@example.test",
            "action": "send_email",
        },
    )

    with workspace_context(workspace.id), pytest.raises(ValidationFailed) as exc:
        services.create_draft(
            membership=sales_rep,
            actor=sales_rep.user,
            purpose=DraftPurpose.SUMMARISE_NOTES,
            activity_ids=[activities[0].id],
        )
    assert exc.value.code == "ai_output_rejected"


def test_non_json_output_is_rejected(workspace, sales_rep, notes, monkeypatch):
    contact, activities = notes

    class _Response:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            return {"output_text": "not json at all", "usage": {}}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            return _Response()

    monkeypatch.setattr(httpx, "Client", _Client)

    with workspace_context(workspace.id), pytest.raises(ValidationFailed):
        services.create_draft(
            membership=sales_rep,
            actor=sales_rep.user,
            purpose=DraftPurpose.SUMMARISE_NOTES,
            activity_ids=[activities[0].id],
        )


# ---------------------------------------------------------------------------
# Failure handling and spend
# ---------------------------------------------------------------------------


def test_a_timeout_holds_the_reservation(workspace, sales_rep, notes, monkeypatch):
    """The provider may have processed it and will bill for it either way."""
    contact, activities = notes
    _raise_on_send(monkeypatch, httpx.ReadTimeout("timed out"))

    with pytest.raises(ValidationFailed) as exc:
        services.create_draft(
            membership=sales_rep,
            actor=sales_rep.user,
            purpose=DraftPurpose.SUMMARISE_NOTES,
            activity_ids=[activities[0].id],
            workspace_id=workspace.id,
        )

    with workspace_context(workspace.id):
        period = budget.current_period()
        status = budget.status()

    assert exc.value.code == "ai_unavailable"
    # Held, not released. This is the assertion the original @transaction.atomic
    # made impossible to satisfy: the rollback took the reservation with it.
    assert period.reserved_usd > 0
    assert status["uncertain_reservations"] == 1


def test_a_definite_client_error_releases_the_reservation(workspace, sales_rep, notes, monkeypatch):
    """A 4xx was never processed, so the budget must go back."""

    class _Response:
        status_code = 400
        headers = {}

        def json(self):
            return {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            return _Response()

    contact, activities = notes
    monkeypatch.setattr(httpx, "Client", _Client)

    with workspace_context(workspace.id):
        with pytest.raises(ValidationFailed):
            services.create_draft(
                membership=sales_rep,
                actor=sales_rep.user,
                purpose=DraftPurpose.SUMMARISE_NOTES,
                activity_ids=[activities[0].id],
            )
        period = budget.current_period()

    assert period.reserved_usd == 0
    assert period.settled_usd == 0


def test_core_work_continues_when_ai_is_over_budget(workspace, sales_rep, notes, settings):
    """No employee is blocked from basic work by an optional component."""
    settings.AI_MONTHLY_CEILING_USD = "0.0000"
    contact, activities = notes

    with workspace_context(workspace.id):
        with pytest.raises(BudgetExceeded):
            services.create_draft(
                membership=sales_rep,
                actor=sales_rep.user,
                purpose=DraftPurpose.SUMMARISE_NOTES,
                activity_ids=[activities[0].id],
            )

        # The ordinary workflow is entirely unaffected.
        activity = crm_services.record_activity(
            membership=sales_rep,
            actor=sales_rep.user,
            contact=contact,
            kind=ActivityKind.CALL,
            occurred_at=timezone.now() - timedelta(minutes=5),
            outcome="Logged by hand, as normal.",
        )
    assert activity.id is not None


def test_recording_a_decision_feeds_the_evaluation(workspace, sales_rep, notes, monkeypatch):
    contact, activities = notes
    _mock_provider(
        monkeypatch,
        {
            "summary": "A usable summary.",
            "source_ids": [str(activities[0].id)],
            "uncertainties": [],
        },
    )

    with workspace_context(workspace.id):
        draft = services.create_draft(
            membership=sales_rep,
            actor=sales_rep.user,
            purpose=DraftPurpose.SUMMARISE_NOTES,
            activity_ids=[activities[0].id],
        )
        rejected = services.record_decision(
            membership=sales_rep,
            actor=sales_rep.user,
            draft_id=draft.id,
            accepted=False,
            reason="Missed that the budget is unconfirmed.",
        )

    assert rejected.rejected_at is not None
    assert rejected.accepted_at is None
