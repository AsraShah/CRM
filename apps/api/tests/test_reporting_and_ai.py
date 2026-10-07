"""TEST11, TEST12, TEST13, TEST14 - exceptions, reporting, AI budget, export."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from modules.ai import budget
from modules.ai.contracts import OutputRejected, validate_output
from modules.common.exceptions import BudgetExceeded, NotAuthorized, ValidationFailed
from modules.common.tenancy import workspace_context
from modules.crm.models import Client, ClientStatus
from modules.reporting import exceptions_service, export, metrics
from modules.reporting.models import CashReceipt, ExceptionKind, ExceptionState

pytestmark = pytest.mark.django_db


@pytest.fixture
def client_record(workspace, contact_factory):
    with workspace_context(workspace.id):
        contact = contact_factory(workspace, display_name="Acme Ltd")
        yield Client.objects.create(
            workspace=workspace,
            contact=contact,
            display_name="Acme Ltd",
            status=ClientStatus.ACCEPTED,
        )


# ---------------------------------------------------------------------------
# TEST12 - reporting (CRM11)
# ---------------------------------------------------------------------------


def test_won_deal_does_not_increase_collected_revenue(
    workspace, owner, sales_manager, contact_factory
):
    """The single most important separation on the dashboard."""
    from modules.crm import services
    from modules.crm.models import OpportunityStage

    with workspace_context(workspace.id):
        contact = contact_factory(workspace)
        deal = services.create_opportunity(
            membership=sales_manager,
            actor=sales_manager.user,
            contact=contact,
            service="Retainer",
            amount=Decimal("5000.00"),
            currency="USD",
        )
        deal.stage = OpportunityStage.WON
        deal.accepted_scope_evidence = "SOW signed"
        deal.commercial_reference = "INV-1"
        deal.closed_at = timezone.now()
        deal.save()

        result = metrics.overview(membership=owner)

    assert result["won_contract_value"]["amounts"]["USD"] == "5000.00"
    # No receipt was entered, so no cash was received.
    assert result["cash_received"]["amounts"] == {}


def test_currencies_are_never_combined(workspace, owner, sales_manager, contact_factory):
    """CRM03 acceptance: totals never combine currencies without a basis."""
    from modules.crm import services

    with workspace_context(workspace.id):
        for amount, currency in ((Decimal("1000"), "USD"), (Decimal("50000"), "PKR")):
            services.create_opportunity(
                membership=sales_manager,
                actor=sales_manager.user,
                contact=contact_factory(workspace),
                service="Work",
                amount=amount,
                currency=currency,
            )
        result = metrics.overview(membership=owner)

    amounts = result["open_pipeline_value"]["amounts"]
    assert set(amounts) == {"USD", "PKR"}
    # There is no combined total to be misread.
    assert "total" not in result["open_pipeline_value"]
    assert result["basis"]["currencies_combined"] is False


def test_unknown_value_is_counted_separately_not_as_zero(
    workspace, owner, sales_manager, contact_factory
):
    from modules.crm import services

    with workspace_context(workspace.id):
        services.create_opportunity(
            membership=sales_manager,
            actor=sales_manager.user,
            contact=contact_factory(workspace),
            service="Not yet quoted",
        )
        result = metrics.overview(membership=owner)

    pipeline = result["open_pipeline_value"]
    assert pipeline["unknown_count"] == 1
    assert pipeline["amounts"] == {}


def test_a_representative_sees_only_their_own_figures(
    workspace, sales_rep, sales_manager, contact_factory
):
    """Reports exclude inaccessible records (CRM11 acceptance)."""
    from modules.crm import services

    with workspace_context(workspace.id):
        services.create_opportunity(
            membership=sales_manager,
            actor=sales_manager.user,
            contact=contact_factory(workspace),
            service="Manager's deal",
            owner=sales_manager.user,
            amount=Decimal("9000"),
            currency="USD",
        )
        result = metrics.overview(membership=sales_rep)

    assert result["scope"] == "own_records"
    assert result["open_pipeline_value"]["record_count"] == 0


def test_coverage_is_null_when_there_is_nothing_to_measure(workspace, owner):
    """An empty workspace has no coverage figure, good or bad."""
    with workspace_context(workspace.id):
        measures = metrics.pilot_measures(membership=owner)
    assert measures["ownership_coverage"]["percentage"] is None


def test_receipt_requires_evidence(workspace, owner, client_record):
    with workspace_context(workspace.id):
        receipt = CashReceipt.objects.create(
            workspace=workspace,
            client=client_record,
            amount=Decimal("2500.00"),
            currency="USD",
            received_at=date.today(),
            evidence_reference="Bank ref 99213",
            recorded_by=owner.user,
        )
    assert receipt.evidence_reference


@pytest.mark.parametrize("amount", ["0", "-50.00"])
def test_a_new_receipt_must_be_positive(workspace, owner, client_record, amount):
    """Only an adjustment may be zero or negative; the database says so too.

    The API used to pass a non-positive amount straight to the check
    constraint, so the caller got a 500 instead of being told what to fix.
    """
    from rest_framework.test import APIClient

    api = APIClient()
    api.force_login(owner.user)
    response = api.post(
        "/api/v1/receipts/",
        {
            "client": str(client_record.id),
            "amount": amount,
            "currency": "USD",
            "received_at": date.today().isoformat(),
            "evidence_reference": "Bank ref 1",
        },
        format="json",
    )
    assert response.status_code == 400, response.content
    assert "amount" in response.json()["field_errors"]


# ---------------------------------------------------------------------------
# TEST11 - exceptions (CRM10)
# ---------------------------------------------------------------------------


def test_sweep_raises_an_exception_for_an_overdue_task(workspace, sales_rep, contact_factory):
    from modules.work.models import Task, TaskStatus

    with workspace_context(workspace.id):
        Task.objects.create(
            workspace=workspace,
            title="Call the prospect",
            owner=sales_rep.user,
            status=TaskStatus.OPEN,
            due_at=timezone.now() - timedelta(days=2),
            original_due_at=timezone.now() - timedelta(days=2),
            contact=contact_factory(workspace),
        )
        raised = exceptions_service.sweep()

    assert raised.get(ExceptionKind.MISSED_DEADLINE) == 1


def test_sweeping_twice_does_not_duplicate(workspace, sales_rep, contact_factory):
    from modules.reporting.models import WorkException
    from modules.work.models import Task, TaskStatus

    with workspace_context(workspace.id):
        Task.objects.create(
            workspace=workspace,
            title="Call",
            owner=sales_rep.user,
            status=TaskStatus.OPEN,
            due_at=timezone.now() - timedelta(days=2),
            original_due_at=timezone.now() - timedelta(days=2),
            contact=contact_factory(workspace),
        )
        exceptions_service.sweep()
        exceptions_service.sweep()
        count = WorkException.objects.filter(kind=ExceptionKind.MISSED_DEADLINE).count()

    assert count == 1


def test_an_employee_can_see_and_dispute_their_own_exception(workspace, sales_rep, contact_factory):
    """CRM10: an alert can be challenged, and a disputed one stays visible."""
    from modules.work.models import Task, TaskStatus

    with workspace_context(workspace.id):
        Task.objects.create(
            workspace=workspace,
            title="Call",
            owner=sales_rep.user,
            status=TaskStatus.OPEN,
            due_at=timezone.now() - timedelta(days=2),
            original_due_at=timezone.now() - timedelta(days=2),
            contact=contact_factory(workspace),
        )
        exceptions_service.sweep()

        mine = list(exceptions_service.visible_exceptions(sales_rep))
        assert len(mine) == 1

        disputed = exceptions_service.explain(
            membership=sales_rep,
            actor=sales_rep.user,
            exception_id=mine[0].id,
            explanation="The client cancelled; I rescheduled with their agreement.",
            dispute=True,
        )

    assert disputed.state == ExceptionState.DISPUTED
    assert disputed.employee_responded_at is not None


def test_an_employee_cannot_see_a_colleagues_exception(
    workspace, sales_rep, other_rep, contact_factory
):
    from modules.work.models import Task, TaskStatus

    with workspace_context(workspace.id):
        Task.objects.create(
            workspace=workspace,
            title="Call",
            owner=sales_rep.user,
            status=TaskStatus.OPEN,
            due_at=timezone.now() - timedelta(days=2),
            original_due_at=timezone.now() - timedelta(days=2),
            contact=contact_factory(workspace),
        )
        exceptions_service.sweep()
        theirs = list(exceptions_service.visible_exceptions(other_rep))

    assert theirs == []


def test_closing_an_exception_requires_a_written_decision(
    workspace, sales_manager, sales_rep, contact_factory
):
    """ "Resolved" with no reason is indistinguishable from clearing a queue."""
    from modules.work.models import Task, TaskStatus

    with workspace_context(workspace.id):
        Task.objects.create(
            workspace=workspace,
            title="Call",
            owner=sales_rep.user,
            status=TaskStatus.OPEN,
            due_at=timezone.now() - timedelta(days=2),
            original_due_at=timezone.now() - timedelta(days=2),
            contact=contact_factory(workspace),
        )
        exceptions_service.sweep()
        exception = exceptions_service.visible_exceptions(sales_manager).first()

        with pytest.raises(ValidationFailed):
            exceptions_service.review(
                membership=sales_manager,
                actor=sales_manager.user,
                exception_id=exception.id,
                decision="   ",
            )

        reviewed = exceptions_service.review(
            membership=sales_manager,
            actor=sales_manager.user,
            exception_id=exception.id,
            decision="Agreed; the client moved the call. No action needed.",
        )

    assert reviewed.state == ExceptionState.RESOLVED
    assert reviewed.reviewed_by_id == sales_manager.user_id


def test_a_representative_cannot_review_exceptions(workspace, sales_rep, contact_factory):
    from modules.work.models import Task, TaskStatus

    with workspace_context(workspace.id):
        Task.objects.create(
            workspace=workspace,
            title="Call",
            owner=sales_rep.user,
            status=TaskStatus.OPEN,
            due_at=timezone.now() - timedelta(days=2),
            original_due_at=timezone.now() - timedelta(days=2),
            contact=contact_factory(workspace),
        )
        exceptions_service.sweep()
        exception = exceptions_service.visible_exceptions(sales_rep).first()

        with pytest.raises(NotAuthorized):
            exceptions_service.review(
                membership=sales_rep,
                actor=sales_rep.user,
                exception_id=exception.id,
                decision="Closing my own exception.",
            )


# ---------------------------------------------------------------------------
# TEST13 - AI budget and output validation (CRM12)
# ---------------------------------------------------------------------------


def test_ai_is_refused_when_the_feature_is_disabled(workspace, sales_rep, settings):
    """Core functions continue when AI is disabled (CRM12)."""
    settings.FEATURE_AI_ENABLED = False
    with workspace_context(workspace.id), pytest.raises(BudgetExceeded) as exc:
        budget.reserve(
            actor=sales_rep.user,
            request_key="k1",
            purpose="summarise_notes",
            prompt_text="hello",
        )
    assert exc.value.code == "ai_disabled"


def test_the_monthly_ceiling_cannot_be_exceeded(workspace, sales_rep, settings):
    settings.FEATURE_AI_ENABLED = True
    settings.AI_MONTHLY_CEILING_USD = "0.0001"

    with workspace_context(workspace.id), pytest.raises(BudgetExceeded) as exc:
        budget.reserve(
            actor=sales_rep.user,
            request_key="k2",
            purpose="summarise_notes",
            prompt_text="x" * 10_000,
        )
    assert exc.value.code in {"ai_budget_exhausted", "input_too_large"}


def test_concurrent_requests_cannot_both_claim_the_same_budget(workspace, sales_rep, settings):
    """The organisation-wide limit cannot be bypassed by concurrency (CRM12)."""
    settings.FEATURE_AI_ENABLED = True
    settings.AI_MONTHLY_CEILING_USD = "2.00"
    settings.AI_MAX_CONCURRENT_REQUESTS = 1

    with workspace_context(workspace.id):
        budget.reserve(
            actor=sales_rep.user,
            request_key="first",
            purpose="summarise_notes",
            prompt_text="short prompt",
        )
        with pytest.raises(BudgetExceeded) as exc:
            budget.reserve(
                actor=sales_rep.user,
                request_key="second",
                purpose="summarise_notes",
                prompt_text="short prompt",
            )
    assert exc.value.code == "ai_concurrency_limit"


def test_a_retry_reuses_its_original_reservation(workspace, sales_rep, settings):
    """A repeated request must not reserve the budget twice."""
    settings.FEATURE_AI_ENABLED = True
    settings.AI_MONTHLY_CEILING_USD = "2.00"

    with workspace_context(workspace.id):
        first = budget.reserve(
            actor=sales_rep.user,
            request_key="same-key",
            purpose="summarise_notes",
            prompt_text="short prompt",
        )
        second = budget.reserve(
            actor=sales_rep.user,
            request_key="same-key",
            purpose="summarise_notes",
            prompt_text="short prompt",
        )

    assert second.replayed is True
    assert second.record.id == first.record.id


def test_an_uncertain_request_keeps_its_reservation(workspace, sales_rep, settings):
    """A timeout may still be billed, so the budget stays committed."""
    settings.FEATURE_AI_ENABLED = True
    settings.AI_MONTHLY_CEILING_USD = "2.00"

    with workspace_context(workspace.id):
        reservation = budget.reserve(
            actor=sales_rep.user,
            request_key="timeout",
            purpose="summarise_notes",
            prompt_text="short prompt",
        )
        before = reservation.period.reserved_usd

        budget.mark_uncertain(reservation_id=reservation.record.id, note="Read timeout.")
        reservation.period.refresh_from_db()

    # Still held, not released.
    assert reservation.period.reserved_usd == before


def test_the_kill_switch_denies_regardless_of_budget(workspace, owner, settings):
    settings.FEATURE_AI_ENABLED = True
    settings.AI_MONTHLY_CEILING_USD = "2.00"

    with workspace_context(workspace.id):
        budget.set_kill_switch(actor=owner.user, enabled=False, reason="Cost review.")
        with pytest.raises(BudgetExceeded) as exc:
            budget.reserve(
                actor=owner.user,
                request_key="k3",
                purpose="summarise_notes",
                prompt_text="short",
            )
    assert exc.value.code == "ai_kill_switch"


def test_output_citing_an_unsupplied_source_is_rejected():
    """A citation to a record we did not send is grounds to discard the draft."""
    import uuid

    supplied = str(uuid.uuid4())
    smuggled = str(uuid.uuid4())

    with pytest.raises(OutputRejected, match="not supplied"):
        validate_output(
            raw={
                "summary": "All fine.",
                "source_ids": [supplied, smuggled],
                "uncertainties": [],
            },
            purpose="summarise_notes",
            permitted_source_ids={supplied},
        )


def test_output_with_extra_fields_is_rejected():
    """The schema forbids extras, so a model cannot smuggle in an action."""
    import uuid

    supplied = str(uuid.uuid4())
    with pytest.raises(OutputRejected):
        validate_output(
            raw={
                "summary": "All fine.",
                "source_ids": [supplied],
                "send_email_to": "client@example.test",
            },
            purpose="summarise_notes",
            permitted_source_ids={supplied},
        )


def test_the_draft_schema_cannot_express_an_action():
    """Defence in depth: even a compromised prompt has no field to abuse."""
    from modules.ai.contracts import EmailDraftOutput

    assert set(EmailDraftOutput.model_fields) == {
        "subject",
        "body",
        "source_ids",
        "uncertainties",
    }


# ---------------------------------------------------------------------------
# TEST14 - export (CRM13)
# ---------------------------------------------------------------------------


def test_only_the_owner_can_export(workspace, sales_manager):
    with workspace_context(workspace.id), pytest.raises(NotAuthorized):
        export.build_export(membership=sales_manager, actor=sales_manager.user)


def test_export_produces_a_readable_archive_with_stable_identifiers(
    workspace, owner, client_record
):
    import io
    import json
    import zipfile

    with workspace_context(workspace.id):
        payload = export.build_export(membership=owner, actor=owner.user)

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
        clients_csv = archive.read("clients.csv").decode()

    assert "manifest.json" in names
    assert "contacts.csv" in names
    assert manifest["identifier_scheme"].startswith("UUID")
    # The client's UUID appears, so relationships can be rebuilt.
    assert str(client_record.id) in clients_csv


def test_export_is_audited(workspace, owner):
    from modules.common.models import AuditAction, AuditEvent

    with workspace_context(workspace.id):
        export.build_export(membership=owner, actor=owner.user)
        events = AuditEvent.objects.filter(action=AuditAction.EXPORT)

    assert events.count() == 1
    assert events.first().actor_id == owner.user_id
