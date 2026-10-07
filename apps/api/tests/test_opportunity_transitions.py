"""TEST04 - stage transitions, concurrency and currency separation (CRM03)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from modules.common.exceptions import (
    TransitionNotAllowed,
    ValidationFailed,
    VersionConflict,
)
from modules.common.tenancy import workspace_context
from modules.crm import services
from modules.crm.models import OpportunityStage, StageHistory

pytestmark = pytest.mark.django_db


@pytest.fixture
def deal(workspace, sales_rep, contact_factory):
    with workspace_context(workspace.id):
        contact = contact_factory(workspace, display_name="Prospect Ltd")
        yield services.create_opportunity(
            membership=sales_rep,
            actor=sales_rep.user,
            contact=contact,
            service="Website build",
            amount=Decimal("5000.00"),
            currency="USD",
        )


def test_valid_transition_appends_stage_history(workspace, sales_rep, deal):
    with workspace_context(workspace.id):
        updated = services.transition_opportunity(
            membership=sales_rep,
            actor=sales_rep.user,
            opportunity_id=deal.id,
            target_stage=OpportunityStage.QUALIFIED,
            expected_version=deal.version,
        )
        assert updated.stage == OpportunityStage.QUALIFIED

        history = list(StageHistory.objects.filter(opportunity=deal).order_by("changed_at"))
        # Creation wrote the first row; this transition wrote the second.
        assert [h.to_stage for h in history] == ["discovery", "qualified"]
        assert history[-1].from_stage == "discovery"


def test_invalid_transition_is_refused(workspace, sales_rep, deal):
    """Discovery cannot jump straight to Negotiation."""
    with workspace_context(workspace.id), pytest.raises(TransitionNotAllowed):
        services.transition_opportunity(
            membership=sales_rep,
            actor=sales_rep.user,
            opportunity_id=deal.id,
            target_stage=OpportunityStage.NEGOTIATION,
            expected_version=deal.version,
        )


def test_stale_update_cannot_overwrite_a_colleagues_change(
    workspace, sales_rep, sales_manager, deal
):
    """CRM03 acceptance: a stale update cannot overwrite a colleague's change."""
    with workspace_context(workspace.id):
        stale_version = deal.version

        services.transition_opportunity(
            membership=sales_manager,
            actor=sales_manager.user,
            opportunity_id=deal.id,
            target_stage=OpportunityStage.QUALIFIED,
            expected_version=stale_version,
        )

        with pytest.raises(VersionConflict) as exc:
            services.transition_opportunity(
                membership=sales_rep,
                actor=sales_rep.user,
                opportunity_id=deal.id,
                target_stage=OpportunityStage.LOST,
                expected_version=stale_version,
                lost_reason="Budget withdrawn",
            )
        # The client is told the current version so the user can reconcile.
        assert exc.value.extra["current_version"] > stale_version


def test_proposal_requires_a_scope_reference(workspace, sales_rep, deal):
    with workspace_context(workspace.id):
        qualified = services.transition_opportunity(
            membership=sales_rep,
            actor=sales_rep.user,
            opportunity_id=deal.id,
            target_stage=OpportunityStage.QUALIFIED,
            expected_version=deal.version,
        )
        with pytest.raises(ValidationFailed, match="scope_reference"):
            services.transition_opportunity(
                membership=sales_rep,
                actor=sales_rep.user,
                opportunity_id=deal.id,
                target_stage=OpportunityStage.PROPOSAL,
                expected_version=qualified.version,
            )


def test_closing_as_lost_requires_a_reason(workspace, sales_rep, deal):
    with workspace_context(workspace.id), pytest.raises(ValidationFailed):
        services.transition_opportunity(
            membership=sales_rep,
            actor=sales_rep.user,
            opportunity_id=deal.id,
            target_stage=OpportunityStage.LOST,
            expected_version=deal.version,
        )


def test_won_must_go_through_conversion(workspace, sales_manager, deal):
    """Winning creates a client and onboarding atomically, so the generic
    transition refuses it rather than closing the deal without them (CRM07)."""
    with workspace_context(workspace.id), pytest.raises(TransitionNotAllowed) as exc:
        services.transition_opportunity(
            membership=sales_manager,
            actor=sales_manager.user,
            opportunity_id=deal.id,
            target_stage=OpportunityStage.WON,
            expected_version=deal.version,
        )
    assert exc.value.code == "use_convert_endpoint"


def test_amount_without_currency_is_refused(workspace, sales_rep, contact_factory):
    """Pipeline totals must never combine currencies without an explicit basis."""
    with workspace_context(workspace.id):
        contact = contact_factory(workspace)
        with pytest.raises(ValidationFailed, match="currency"):
            services.create_opportunity(
                membership=sales_rep,
                actor=sales_rep.user,
                contact=contact,
                service="Retainer",
                amount=Decimal("1200.00"),
                currency="",
            )


def test_unknown_value_stays_null_rather_than_zero(workspace, sales_rep, contact_factory):
    """Null means unknown; zero is a real value (section 4.1)."""
    with workspace_context(workspace.id):
        contact = contact_factory(workspace)
        opportunity = services.create_opportunity(
            membership=sales_rep,
            actor=sales_rep.user,
            contact=contact,
            service="Scoping call",
        )
        assert opportunity.amount is None
        assert opportunity.currency == ""


def test_a_rep_cannot_transition_another_reps_deal(workspace, sales_rep, other_rep, deal):
    """CRM01 acceptance: changing an identifier must not reach another's record."""
    from modules.common.exceptions import NotAuthorized

    with workspace_context(workspace.id), pytest.raises(NotAuthorized):
        services.transition_opportunity(
            membership=other_rep,
            actor=other_rep.user,
            opportunity_id=deal.id,
            target_stage=OpportunityStage.QUALIFIED,
            expected_version=deal.version,
        )
