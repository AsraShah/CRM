"""TEST10 - ticket lifecycle and note visibility (CRM09)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from modules.common.exceptions import (
    NotAuthorized,
    TransitionNotAllowed,
    ValidationFailed,
)
from modules.common.tenancy import workspace_context
from modules.identity.models import Role
from modules.support import services
from modules.support.models import (
    CommentVisibility,
    TicketPriority,
    TicketState,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def delivery_manager(workspace):
    from tests.conftest import _make_member

    return _make_member(workspace, "delivery@scalevexo.test", Role.DELIVERY_MANAGER)


@pytest.fixture
def delivery_employee(workspace):
    from tests.conftest import _make_member

    return _make_member(workspace, "dev@scalevexo.test", Role.DELIVERY_EMPLOYEE)


@pytest.fixture
def ticket(workspace, delivery_employee):
    with workspace_context(workspace.id):
        yield services.create_ticket(
            membership=delivery_employee,
            actor=delivery_employee.user,
            title="Contact form is failing",
            description="Reported by the client this morning.",
        )


def _advance(membership, ticket, target, **kwargs):
    return services.transition_ticket(
        membership=membership,
        actor=membership.user,
        ticket_id=ticket.id,
        target_state=target,
        expected_version=ticket.version,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Resolution evidence
# ---------------------------------------------------------------------------


def test_resolution_requires_a_note_and_a_closure_test(workspace, delivery_employee, ticket):
    """CRM09 acceptance: a ticket cannot be resolved without both."""
    with workspace_context(workspace.id):
        ticket = _advance(delivery_employee, ticket, TicketState.TRIAGED)
        ticket = _advance(delivery_employee, ticket, TicketState.IN_PROGRESS)

        with pytest.raises(ValidationFailed) as exc:
            _advance(
                delivery_employee,
                ticket,
                TicketState.RESOLVED,
                resolution_note="Fixed it.",
                closure_test_result="",
            )
    assert "closure_test_result" in exc.value.field_errors


def test_full_resolution_records_its_evidence(workspace, delivery_employee, ticket):
    with workspace_context(workspace.id):
        ticket = _advance(delivery_employee, ticket, TicketState.TRIAGED)
        ticket = _advance(delivery_employee, ticket, TicketState.IN_PROGRESS)
        resolved = _advance(
            delivery_employee,
            ticket,
            TicketState.RESOLVED,
            resolution_note="Corrected the SMTP credentials.",
            closure_test_result="Submitted the form on Safari and Chrome; both arrived.",
        )

    assert resolved.state == TicketState.RESOLVED
    assert resolved.resolved_by_id == delivery_employee.user_id


# ---------------------------------------------------------------------------
# Waiting states
# ---------------------------------------------------------------------------


def test_waiting_requires_a_next_owner_and_a_review_time(workspace, delivery_employee, ticket):
    """Otherwise a ticket waits forever and nobody notices."""
    with workspace_context(workspace.id):
        ticket = _advance(delivery_employee, ticket, TicketState.TRIAGED)

        with pytest.raises(ValidationFailed):
            _advance(
                delivery_employee,
                ticket,
                TicketState.WAITING_CUSTOMER,
                waiting_reason="Waiting for the client to confirm.",
            )

        waiting = _advance(
            delivery_employee,
            ticket,
            TicketState.WAITING_CUSTOMER,
            waiting_reason="Waiting for the client to confirm the fix.",
            waiting_next_owner_id=delivery_employee.user_id,
            waiting_review_at=timezone.now() + timedelta(days=2),
        )

    assert waiting.state == TicketState.WAITING_CUSTOMER
    assert waiting.waiting_next_owner_id == delivery_employee.user_id


def test_the_two_waiting_states_are_distinct(workspace, delivery_employee, ticket):
    """ "Waiting on the client" and "waiting on us" are different problems."""
    assert TicketState.WAITING_CUSTOMER != TicketState.WAITING_INTERNAL


# ---------------------------------------------------------------------------
# Reopening
# ---------------------------------------------------------------------------


def test_reopening_preserves_the_prior_resolution(workspace, delivery_employee, ticket):
    """CRM09 acceptance: reopening preserves prior resolution history."""
    with workspace_context(workspace.id):
        ticket = _advance(delivery_employee, ticket, TicketState.TRIAGED)
        ticket = _advance(delivery_employee, ticket, TicketState.IN_PROGRESS)
        ticket = _advance(
            delivery_employee,
            ticket,
            TicketState.RESOLVED,
            resolution_note="Corrected the SMTP credentials.",
            closure_test_result="Tested on Safari and Chrome.",
        )

        reopened = _advance(
            delivery_employee,
            ticket,
            TicketState.TRIAGED,
            reason="The client says it is still failing on mobile.",
        )
        history = list(reopened.state_history.order_by("changed_at"))

    assert reopened.state == TicketState.TRIAGED
    assert reopened.reopen_count == 1
    # The live fields are cleared, but the earlier claim survives on the
    # history row.
    assert reopened.resolution_note == ""
    assert "SMTP credentials" in history[-1].superseded_resolution


def test_reopening_requires_a_reason(workspace, delivery_employee, ticket):
    with workspace_context(workspace.id):
        ticket = _advance(delivery_employee, ticket, TicketState.TRIAGED)
        ticket = _advance(delivery_employee, ticket, TicketState.IN_PROGRESS)
        ticket = _advance(
            delivery_employee,
            ticket,
            TicketState.RESOLVED,
            resolution_note="Fixed.",
            closure_test_result="Verified.",
        )
        with pytest.raises(ValidationFailed):
            _advance(delivery_employee, ticket, TicketState.TRIAGED, reason="")


def test_invalid_transition_is_refused(workspace, delivery_employee, ticket):
    """A brand new ticket cannot jump straight to resolved."""
    with workspace_context(workspace.id), pytest.raises(TransitionNotAllowed):
        _advance(
            delivery_employee,
            ticket,
            TicketState.RESOLVED,
            resolution_note="Fixed.",
            closure_test_result="Verified.",
        )


# ---------------------------------------------------------------------------
# Comment visibility — the property that matters most in this module
# ---------------------------------------------------------------------------


def test_comments_are_internal_by_default(workspace, delivery_employee, ticket):
    """The asymmetry of the two mistakes is why this default is not negotiable."""
    with workspace_context(workspace.id):
        comment = services.add_comment(
            membership=delivery_employee,
            actor=delivery_employee.user,
            ticket_id=ticket.id,
            body="The client was unclear about what they actually want here.",
        )
    assert comment.visibility == CommentVisibility.INTERNAL


def test_only_a_manager_can_publish_to_the_client(
    workspace, delivery_employee, delivery_manager, ticket
):
    with workspace_context(workspace.id):
        with pytest.raises(NotAuthorized):
            services.add_comment(
                membership=delivery_employee,
                actor=delivery_employee.user,
                ticket_id=ticket.id,
                body="We will have this fixed by Friday.",
                visibility=CommentVisibility.CLIENT_VISIBLE,
            )

        published = services.add_comment(
            membership=delivery_manager,
            actor=delivery_manager.user,
            ticket_id=ticket.id,
            body="We will have this fixed by Friday.",
            visibility=CommentVisibility.CLIENT_VISIBLE,
        )
    assert published.visibility == CommentVisibility.CLIENT_VISIBLE


def test_a_client_sees_only_published_comments(
    workspace, delivery_employee, delivery_manager, ticket
):
    """The Release 3 portal reads this same selector.

    Written and tested now so the portal inherits correct behaviour rather than
    needing a second query somebody has to remember to get right.
    """
    from tests.conftest import _make_member

    with workspace_context(workspace.id):
        services.add_comment(
            membership=delivery_employee,
            actor=delivery_employee.user,
            ticket_id=ticket.id,
            body="Internal: this is the third time they have broken this.",
        )
        services.add_comment(
            membership=delivery_manager,
            actor=delivery_manager.user,
            ticket_id=ticket.id,
            body="We are looking into it now.",
            visibility=CommentVisibility.CLIENT_VISIBLE,
        )

        staff_view = list(services.visible_comments(delivery_employee, ticket))

        client_membership = _make_member(workspace, "client@acme.test", Role.CLIENT)
        client_view = list(services.visible_comments(client_membership, ticket))

    assert len(staff_view) == 2
    assert len(client_view) == 1
    assert "third time" not in client_view[0].body


def test_critical_ticket_emits_an_event_for_the_incident_owner(workspace, delivery_employee):
    """Rule A07 notifies on a critical ticket; the event must carry the flag."""
    from modules.automation.models import OutboxEvent

    with workspace_context(workspace.id):
        services.create_ticket(
            membership=delivery_employee,
            actor=delivery_employee.user,
            title="Production site is down",
            priority=TicketPriority.CRITICAL,
        )
        event = OutboxEvent.objects.filter(event_type="ticket.created").latest("occurred_at")

    assert event.payload["critical"] is True
