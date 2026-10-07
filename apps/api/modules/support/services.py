"""Ticket services (CRM09).

Resolution requires both a note and a closure test result. Reopening preserves
prior resolution history. Internal notes stay internal unless somebody
deliberately makes them client-visible.
"""

from __future__ import annotations

import uuid

from django.db import transaction
from django.utils import timezone

from modules.automation import outbox
from modules.common.audit import AuditAction, diff_fields, record_audit, snapshot
from modules.common.exceptions import (
    NotAuthorized,
    TransitionNotAllowed,
    ValidationFailed,
)
from modules.common.tenancy import assert_same_workspace, current_workspace_id
from modules.identity.models import Membership, Role, User
from modules.identity.policy import has_permission
from modules.identity.services import assert_expected_version
from modules.support.models import (
    CommentVisibility,
    Ticket,
    TicketComment,
    TicketOrigin,
    TicketPriority,
    TicketState,
    TicketStateHistory,
)

ALLOWED_TICKET_TRANSITIONS: dict[str, frozenset[str]] = {
    TicketState.NEW: frozenset({TicketState.TRIAGED, TicketState.CLOSED}),
    TicketState.TRIAGED: frozenset(
        {
            TicketState.IN_PROGRESS,
            TicketState.WAITING_CUSTOMER,
            TicketState.WAITING_INTERNAL,
            TicketState.CLOSED,
        }
    ),
    TicketState.IN_PROGRESS: frozenset(
        {
            TicketState.WAITING_CUSTOMER,
            TicketState.WAITING_INTERNAL,
            TicketState.RESOLVED,
        }
    ),
    TicketState.WAITING_CUSTOMER: frozenset(
        {TicketState.IN_PROGRESS, TicketState.RESOLVED, TicketState.CLOSED}
    ),
    TicketState.WAITING_INTERNAL: frozenset({TicketState.IN_PROGRESS, TicketState.RESOLVED}),
    # Reopening returns to triage, not straight to in-progress: somebody has to
    # look at why it came back.
    TicketState.RESOLVED: frozenset({TicketState.CLOSED, TicketState.TRIAGED}),
    TicketState.CLOSED: frozenset({TicketState.TRIAGED}),
}

WAITING_STATES = frozenset({TicketState.WAITING_CUSTOMER, TicketState.WAITING_INTERNAL})


def _require(membership: Membership, permission: str) -> None:
    if not has_permission(membership, permission):
        raise NotAuthorized(f"This role may not perform {permission}.")


@transaction.atomic
def create_ticket(
    *,
    membership: Membership,
    actor: User,
    title: str,
    description: str = "",
    origin: str = TicketOrigin.CLIENT_REQUEST,
    priority: str = TicketPriority.NORMAL,
    client=None,
    project=None,
    milestone=None,
    owner_id: uuid.UUID | None = None,
    request_id: str = "",
) -> Ticket:
    _require(membership, "ticket.manage")

    if not title.strip():
        raise ValidationFailed(
            "A ticket needs a title.",
            field_errors={"title": ["This field is required."]},
        )
    if priority not in TicketPriority.values:
        raise ValidationFailed("Unknown priority.")

    assert_same_workspace(*(o for o in (client, project, milestone) if o is not None))

    owner = None
    if owner_id is not None:
        owner_membership = (
            Membership.objects.select_related("user")
            .filter(workspace_id=current_workspace_id(), user_id=owner_id)
            .first()
        )
        if owner_membership is None or not owner_membership.can_receive_assignment:
            raise ValidationFailed(
                "That person cannot take this ticket.",
                field_errors={"owner_id": ["Not available for assignment."]},
            )
        owner = owner_membership.user

    ticket = Ticket.objects.create(
        workspace_id=current_workspace_id(),
        title=title.strip(),
        description=description,
        origin=origin,
        priority=priority,
        state=TicketState.NEW,
        client=client,
        project=project,
        milestone=milestone,
        owner=owner,
        raised_by=actor,
    )

    TicketStateHistory.objects.create(
        workspace_id=ticket.workspace_id,
        ticket=ticket,
        from_state="",
        to_state=TicketState.NEW,
        actor=actor,
        reason="Ticket raised",
    )
    record_audit(
        action=AuditAction.CREATE,
        entity_type="support.Ticket",
        entity_id=ticket.id,
        actor=actor,
        changes={"title": ticket.title, "priority": priority},
        request_id=request_id,
    )
    outbox.emit(
        event_type="ticket.created",
        aggregate_type="ticket",
        aggregate_id=ticket.id,
        aggregate_version=ticket.version,
        # Rule A07 notifies the configured incident owner on a critical ticket.
        payload={"priority": priority, "critical": priority == TicketPriority.CRITICAL},
    )
    return ticket


@transaction.atomic
def transition_ticket(
    *,
    membership: Membership,
    actor: User,
    ticket_id: uuid.UUID,
    target_state: str,
    expected_version: int,
    reason: str = "",
    waiting_reason: str = "",
    waiting_next_owner_id: uuid.UUID | None = None,
    waiting_review_at=None,
    resolution_note: str = "",
    closure_test_result: str = "",
    request_id: str = "",
) -> Ticket:
    """Move a ticket, validating what the target state requires."""
    _require(membership, "ticket.manage")

    workspace_id = current_workspace_id()
    ticket = Ticket.objects.select_for_update().get(
        pk=ticket_id, workspace_id=workspace_id, deleted_at__isnull=True
    )
    assert_expected_version(ticket, expected_version)

    if target_state not in TicketState.values:
        raise ValidationFailed("Unknown ticket state.")

    current = ticket.state
    allowed = ALLOWED_TICKET_TRANSITIONS.get(current, frozenset())
    if target_state not in allowed:
        raise TransitionNotAllowed(
            f"A ticket cannot move from {current} to {target_state}.",
            extra={"from_state": current, "allowed": sorted(allowed)},
        )

    before = snapshot(ticket, ["state", "resolution_note", "waiting_reason"])
    superseded = ""

    if target_state in WAITING_STATES:
        # A waiting ticket with no next owner and no review time waits forever
        # and nobody notices (CRM09).
        if not waiting_reason.strip():
            raise ValidationFailed(
                "Say what this ticket is waiting for.",
                field_errors={"waiting_reason": ["This field is required."]},
            )
        if waiting_next_owner_id is None or waiting_review_at is None:
            raise ValidationFailed(
                "A waiting ticket needs a next owner and a review time.",
                field_errors={
                    "waiting_next_owner_id": ["Required when waiting."],
                    "waiting_review_at": ["Required when waiting."],
                },
            )
        next_owner = (
            Membership.objects.select_related("user")
            .filter(workspace_id=workspace_id, user_id=waiting_next_owner_id)
            .first()
        )
        if next_owner is None or not next_owner.can_receive_assignment:
            raise ValidationFailed(
                "Name an active member to pick this up.",
                field_errors={"waiting_next_owner_id": ["Not available."]},
            )
        ticket.waiting_reason = waiting_reason.strip()
        ticket.waiting_next_owner = next_owner.user
        ticket.waiting_review_at = waiting_review_at

    elif target_state == TicketState.RESOLVED:
        # "It works now" without saying how it was checked is not evidence.
        errors: dict[str, list[str]] = {}
        if not resolution_note.strip():
            errors["resolution_note"] = ["Describe how this was resolved."]
        if not closure_test_result.strip():
            errors["closure_test_result"] = ["Record how the fix was verified."]
        if errors:
            raise ValidationFailed(
                "Resolving a ticket requires a resolution note and a closure test result.",
                field_errors=errors,
            )
        ticket.resolution_note = resolution_note.strip()
        ticket.closure_test_result = closure_test_result.strip()
        ticket.resolved_at = timezone.now()
        ticket.resolved_by = actor
        ticket.waiting_reason = ""
        ticket.waiting_next_owner = None
        ticket.waiting_review_at = None

    elif target_state == TicketState.TRIAGED and current in {
        TicketState.RESOLVED,
        TicketState.CLOSED,
    }:
        # Reopening. Preserve what was previously claimed to be fixed, on the
        # history row, before clearing the live fields (CRM09).
        if not reason.strip():
            raise ValidationFailed(
                "Reopening a ticket requires a reason.",
                field_errors={"reason": ["This field is required."]},
            )
        superseded = (
            f"{ticket.resolution_note}\n\nClosure test: {ticket.closure_test_result}"
        ).strip()
        ticket.reopen_count += 1
        ticket.resolution_note = ""
        ticket.closure_test_result = ""
        ticket.resolved_at = None
        ticket.resolved_by = None
        ticket.closed_at = None

    elif target_state == TicketState.CLOSED:
        ticket.closed_at = timezone.now()

    ticket.state = target_state
    ticket.version += 1
    ticket.save()

    TicketStateHistory.objects.create(
        workspace_id=ticket.workspace_id,
        ticket=ticket,
        from_state=current,
        to_state=target_state,
        actor=actor,
        reason=reason,
        superseded_resolution=superseded,
    )
    record_audit(
        action=AuditAction.TRANSITION,
        entity_type="support.Ticket",
        entity_id=ticket.id,
        actor=actor,
        changes=diff_fields(
            before, snapshot(ticket, ["state", "resolution_note", "waiting_reason"])
        ),
        reason=reason,
        request_id=request_id,
    )
    outbox.emit(
        event_type="ticket.state_changed",
        aggregate_type="ticket",
        aggregate_id=ticket.id,
        aggregate_version=ticket.version,
        payload={"from_state": current, "to_state": target_state},
    )
    return ticket


@transaction.atomic
def add_comment(
    *,
    membership: Membership,
    actor: User,
    ticket_id: uuid.UUID,
    body: str,
    visibility: str = CommentVisibility.INTERNAL,
    request_id: str = "",
) -> TicketComment:
    """Append a comment.

    Visibility defaults to internal and stays internal unless explicitly set
    otherwise. Making a comment client-visible is a deliberate act, restricted
    to roles that deal with clients, and audited — because an internal note the
    client can read is the kind of mistake that ends a relationship.
    """
    _require(membership, "ticket.manage")

    if not body.strip():
        raise ValidationFailed(
            "A comment needs content.",
            field_errors={"body": ["This field is required."]},
        )
    if visibility not in CommentVisibility.values:
        raise ValidationFailed("Unknown visibility.")

    if visibility == CommentVisibility.CLIENT_VISIBLE and membership.role not in {
        Role.OWNER,
        Role.DELIVERY_MANAGER,
        Role.SALES_MANAGER,
    }:
        raise NotAuthorized("Only a manager may publish a comment to the client.")

    ticket = Ticket.objects.get(
        pk=ticket_id, workspace_id=current_workspace_id(), deleted_at__isnull=True
    )

    comment = TicketComment.objects.create(
        workspace_id=ticket.workspace_id,
        ticket=ticket,
        author=actor,
        body=body.strip(),
        visibility=visibility,
    )
    record_audit(
        action=AuditAction.CREATE,
        entity_type="support.TicketComment",
        entity_id=comment.id,
        actor=actor,
        # The body is not audited: it can carry client content, and the comment
        # itself is already the durable record.
        changes={"ticket": str(ticket.id), "visibility": visibility},
        request_id=request_id,
    )
    return comment


def visible_comments(membership: Membership, ticket: Ticket):
    """Comments this actor may read.

    Every staff role sees internal comments. The filter exists so that the
    Release 3 client role reads the *same* selector and gets only what was
    explicitly published — rather than a separate query somebody has to
    remember to write correctly.
    """
    queryset = TicketComment.objects.filter(
        workspace_id=ticket.workspace_id, ticket=ticket
    ).select_related("author")

    if membership.role == Role.CLIENT:
        return queryset.filter(visibility=CommentVisibility.CLIENT_VISIBLE).order_by("created_at")
    return queryset.order_by("created_at")


@transaction.atomic
def set_next_action(
    *,
    membership: Membership,
    actor: User,
    ticket_id: uuid.UUID,
    next_action: str,
    expected_version: int,
    request_id: str = "",
) -> Ticket:
    """Record what happens next on a ticket (CRM09).

    Kept separate from the state transition: the next step changes far more
    often than the state, and should not require moving the ticket to say it.
    """
    _require(membership, "ticket.manage")
    ticket = Ticket.objects.select_for_update().get(
        pk=ticket_id, workspace_id=current_workspace_id(), deleted_at__isnull=True
    )
    assert_expected_version(ticket, expected_version)

    before = snapshot(ticket, ["next_action"])
    ticket.next_action = next_action.strip()
    ticket.version += 1
    ticket.save(update_fields=["next_action", "version", "updated_at"])
    record_audit(
        action=AuditAction.UPDATE,
        entity_type="support.Ticket",
        entity_id=ticket.id,
        actor=actor,
        changes=diff_fields(before, snapshot(ticket, ["next_action"])),
        request_id=request_id,
    )
    return ticket
