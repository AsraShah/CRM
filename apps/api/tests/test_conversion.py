"""TEST08 - won conversion, rollback, retry and delivery acceptance (CRM07)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import transaction

from modules.common.exceptions import (
    NotAuthorized,
    TransitionNotAllowed,
    ValidationFailed,
)
from modules.common.tenancy import workspace_context
from modules.crm import conversion, services
from modules.crm.models import Client, ClientStatus, Opportunity, OpportunityStage
from modules.identity.models import Role
from modules.work.models import Milestone, Project, ProjectStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def delivery_manager(workspace):
    from tests.conftest import _make_member

    return _make_member(workspace, "delivery@scalevexo.test", Role.DELIVERY_MANAGER)


@pytest.fixture
def negotiating_deal(workspace, sales_manager, contact_factory):
    """A deal in negotiation, ready to be won."""
    with workspace_context(workspace.id):
        contact = contact_factory(workspace, display_name="Acme Ltd")
        deal = services.create_opportunity(
            membership=sales_manager,
            actor=sales_manager.user,
            contact=contact,
            service="Website rebuild",
            amount=Decimal("12000.00"),
            currency="USD",
        )
        for target, extra in (
            (OpportunityStage.QUALIFIED, {}),
            (OpportunityStage.PROPOSAL, {"scope_reference": "SOW-2026-014"}),
            (OpportunityStage.NEGOTIATION, {}),
        ):
            deal = services.transition_opportunity(
                membership=sales_manager,
                actor=sales_manager.user,
                opportunity_id=deal.id,
                target_stage=target,
                expected_version=deal.version,
                **extra,
            )
        yield deal


def _convert(sales_manager, deal, delivery_manager, **overrides):
    payload = {
        "membership": sales_manager,
        "actor": sales_manager.user,
        "opportunity_id": deal.id,
        "expected_version": deal.version,
        "accepted_scope_evidence": "Signed SOW-2026-014, five pages.",
        "commercial_reference": "INV-2026-0042",
        "delivery_owner_id": delivery_manager.user_id,
    }
    payload.update(overrides)
    return conversion.convert_won_deal(**payload)


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_conversion_creates_client_and_onboarding_atomically(
    workspace, sales_manager, delivery_manager, negotiating_deal
):
    with workspace_context(workspace.id):
        result = _convert(sales_manager, negotiating_deal, delivery_manager)

        assert result.opportunity.stage == OpportunityStage.WON
        assert result.client.status == ClientStatus.PENDING_HANDOVER
        assert result.project.status == ProjectStatus.PLANNED
        # Template milestones were instantiated.
        assert Milestone.objects.filter(project=result.project).count() == 4
        assert result.was_existing is False


def test_won_does_not_mean_paid(workspace, sales_manager, delivery_manager, negotiating_deal):
    """CRM11: a closed-won deal does not increase collected revenue."""
    from modules.reporting.models import CashReceipt

    with workspace_context(workspace.id):
        _convert(sales_manager, negotiating_deal, delivery_manager)
        assert CashReceipt.objects.count() == 0


def test_sales_history_remains_accessible_to_delivery(
    workspace, sales_manager, delivery_manager, negotiating_deal
):
    """Delivery sees what sales promised without asking for a spreadsheet."""
    with workspace_context(workspace.id):
        result = _convert(sales_manager, negotiating_deal, delivery_manager)
        client = Client.objects.get(pk=result.client.pk)

    assert client.accepted_scope == "Signed SOW-2026-014, five pages."
    assert client.commercial_reference == "INV-2026-0042"
    assert client.originating_opportunity_id == negotiating_deal.id


# ---------------------------------------------------------------------------
# Required evidence
# ---------------------------------------------------------------------------


def test_conversion_requires_scope_evidence(
    workspace, sales_manager, delivery_manager, negotiating_deal
):
    with workspace_context(workspace.id), pytest.raises(ValidationFailed) as exc:
        _convert(
            sales_manager,
            negotiating_deal,
            delivery_manager,
            accepted_scope_evidence="   ",
        )
    assert "accepted_scope_evidence" in exc.value.field_errors


def test_conversion_requires_a_delivery_owner(workspace, sales_manager, negotiating_deal):
    """Missing delivery ownership is an exception, not a silent success."""
    with workspace_context(workspace.id), pytest.raises(ValidationFailed) as exc:
        conversion.convert_won_deal(
            membership=sales_manager,
            actor=sales_manager.user,
            opportunity_id=negotiating_deal.id,
            expected_version=negotiating_deal.version,
            accepted_scope_evidence="Signed SOW",
            commercial_reference="INV-1",
            delivery_owner_id=None,
        )
    assert exc.value.code == "missing_delivery_owner"


def test_delivery_owner_must_hold_a_delivery_role(
    workspace, sales_manager, sales_rep, negotiating_deal
):
    with workspace_context(workspace.id), pytest.raises(ValidationFailed):
        _convert(sales_manager, negotiating_deal, sales_rep)


def test_only_a_negotiating_deal_can_be_won(
    workspace, sales_manager, delivery_manager, contact_factory
):
    with workspace_context(workspace.id):
        contact = contact_factory(workspace)
        early = services.create_opportunity(
            membership=sales_manager,
            actor=sales_manager.user,
            contact=contact,
            service="Discovery only",
        )
        with pytest.raises(TransitionNotAllowed):
            _convert(sales_manager, early, delivery_manager)


def test_a_representative_cannot_convert(workspace, sales_rep, delivery_manager, negotiating_deal):
    """Conversion creates a delivery commitment, so it sits above rep level."""
    with workspace_context(workspace.id), pytest.raises(NotAuthorized):
        _convert(sales_rep, negotiating_deal, delivery_manager)


# ---------------------------------------------------------------------------
# Idempotency and atomicity
# ---------------------------------------------------------------------------


def test_retrying_conversion_creates_no_duplicate(
    workspace, sales_manager, delivery_manager, negotiating_deal
):
    """CRM07 acceptance: retrying creates no duplicate client or project."""
    with workspace_context(workspace.id):
        first = _convert(sales_manager, negotiating_deal, delivery_manager)

        second = conversion.convert_won_deal(
            membership=sales_manager,
            actor=sales_manager.user,
            opportunity_id=negotiating_deal.id,
            expected_version=first.opportunity.version,
            accepted_scope_evidence="Signed SOW-2026-014, five pages.",
            commercial_reference="INV-2026-0042",
            delivery_owner_id=delivery_manager.user_id,
        )

        assert second.was_existing is True
        assert second.client.id == first.client.id
        assert second.project.id == first.project.id
        assert Client.objects.count() == 1
        assert Project.objects.count() == 1


def test_a_failed_conversion_leaves_nothing_behind(
    workspace, sales_manager, delivery_manager, negotiating_deal
):
    """The deal must not end up won without its client and project.

    Forces a failure after the won state is written but before the transaction
    commits, then asserts that the whole thing rolled back.
    """
    with workspace_context(workspace.id):
        with pytest.raises(ValidationFailed), transaction.atomic():
            _convert(
                sales_manager,
                negotiating_deal,
                delivery_manager,
                template_key="does_not_exist",
            )

        deal = Opportunity.objects.get(pk=negotiating_deal.pk)
        assert deal.stage == OpportunityStage.NEGOTIATION
        assert Client.objects.count() == 0
        assert Project.objects.count() == 0


def test_repeat_customer_reuses_the_existing_client(
    workspace, sales_manager, delivery_manager, negotiating_deal, contact_factory
):
    """A second deal with the same contact is not a second client."""
    with workspace_context(workspace.id):
        first = _convert(sales_manager, negotiating_deal, delivery_manager)

        second_deal = services.create_opportunity(
            membership=sales_manager,
            actor=sales_manager.user,
            contact=negotiating_deal.contact,
            service="Phase two",
        )
        for target, extra in (
            (OpportunityStage.QUALIFIED, {}),
            (OpportunityStage.PROPOSAL, {"scope_reference": "SOW-2026-020"}),
            (OpportunityStage.NEGOTIATION, {}),
        ):
            second_deal = services.transition_opportunity(
                membership=sales_manager,
                actor=sales_manager.user,
                opportunity_id=second_deal.id,
                target_stage=target,
                expected_version=second_deal.version,
                **extra,
            )

        second = _convert(sales_manager, second_deal, delivery_manager)

        assert second.client.id == first.client.id
        # But a distinct onboarding project.
        assert second.project.id != first.project.id
        assert Client.objects.count() == 1


# ---------------------------------------------------------------------------
# Handover acceptance
# ---------------------------------------------------------------------------


def test_delivery_accepts_the_handover(
    workspace, sales_manager, delivery_manager, negotiating_deal
):
    with workspace_context(workspace.id):
        result = _convert(sales_manager, negotiating_deal, delivery_manager)

        client = conversion.accept_handover(
            membership=delivery_manager,
            actor=delivery_manager.user,
            client_id=result.client.id,
            expected_version=result.client.version,
        )
        project = Project.objects.get(pk=result.project.pk)

    assert client.status == ClientStatus.ACCEPTED
    assert client.handover_accepted_by_id == delivery_manager.user_id
    # Accepting the handover starts the project.
    assert project.status == ProjectStatus.IN_PROGRESS


def test_returning_a_handover_requires_specifics(
    workspace, sales_manager, delivery_manager, negotiating_deal
):
    """A bare rejection leaves sales guessing what to fix."""
    with workspace_context(workspace.id):
        result = _convert(sales_manager, negotiating_deal, delivery_manager)

        with pytest.raises(ValidationFailed):
            conversion.return_handover(
                membership=delivery_manager,
                actor=delivery_manager.user,
                client_id=result.client.id,
                expected_version=result.client.version,
                missing_information="   ",
            )

        returned = conversion.return_handover(
            membership=delivery_manager,
            actor=delivery_manager.user,
            client_id=result.client.id,
            expected_version=result.client.version,
            missing_information="No access credentials and no named client contact.",
        )

    assert returned.handover_returned_at is not None
    # Still pending: returning is not rejecting outright.
    assert returned.status == ClientStatus.PENDING_HANDOVER
    assert "credentials" in returned.handover_returned_reason


def test_sales_cannot_accept_its_own_handover(
    workspace, sales_manager, delivery_manager, negotiating_deal
):
    """Acceptance is delivery's decision, not the seller's."""
    with workspace_context(workspace.id):
        result = _convert(sales_manager, negotiating_deal, delivery_manager)
        with pytest.raises(NotAuthorized):
            conversion.accept_handover(
                membership=sales_manager,
                actor=sales_manager.user,
                client_id=result.client.id,
                expected_version=result.client.version,
            )
