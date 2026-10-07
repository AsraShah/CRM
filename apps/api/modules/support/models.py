"""Tickets and comments (CRM09).

The single most important property in this module: **internal notes never
inherit client visibility automatically.** A ticket comment defaults to
internal, and making it client-visible is an explicit, audited act. The client
portal arrives in Release 3, and when it does it must find that the visibility
decision was already made correctly on every historical comment — not
retrofitted by a migration guessing at intent.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from modules.common.models import WorkspaceScopedModel


class TicketPriority(models.TextChoices):
    LOW = "low", "Low"
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"
    # Raises a visible management alert on creation (rule A07).
    CRITICAL = "critical", "Critical"


class TicketState(models.TextChoices):
    """SVX-PRD-001 section 5.1.

    The two waiting states are separate because "we are waiting on the client"
    and "we are waiting on ourselves" are different management problems. A
    single "waiting" state hides which one a ticket is in.
    """

    NEW = "new", "New"
    TRIAGED = "triaged", "Triaged"
    IN_PROGRESS = "in_progress", "In progress"
    WAITING_CUSTOMER = "waiting_customer", "Waiting on the customer"
    WAITING_INTERNAL = "waiting_internal", "Waiting on us"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


OPEN_TICKET_STATES = frozenset(
    {
        TicketState.NEW,
        TicketState.TRIAGED,
        TicketState.IN_PROGRESS,
        TicketState.WAITING_CUSTOMER,
        TicketState.WAITING_INTERNAL,
    }
)


class TicketOrigin(models.TextChoices):
    CLIENT_REQUEST = "client_request", "Client request"
    INTERNAL_ISSUE = "internal_issue", "Internal issue"


class Ticket(WorkspaceScopedModel):
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True, default="")
    origin = models.CharField(
        max_length=20, choices=TicketOrigin.choices, default=TicketOrigin.CLIENT_REQUEST
    )
    priority = models.CharField(
        max_length=16, choices=TicketPriority.choices, default=TicketPriority.NORMAL
    )
    state = models.CharField(max_length=20, choices=TicketState.choices, default=TicketState.NEW)

    client = models.ForeignKey(
        "crm.Client",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="tickets",
    )
    project = models.ForeignKey(
        "work.Project",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tickets",
    )
    milestone = models.ForeignKey(
        "work.Milestone",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tickets",
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_tickets",
    )
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )

    # A waiting ticket needs a next owner and a review time, or it waits
    # forever and nobody notices (CRM09).
    waiting_reason = models.TextField(blank=True, default="")
    waiting_next_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    waiting_review_at = models.DateTimeField(null=True, blank=True)
    next_action = models.CharField(max_length=300, blank=True, default="")

    # Resolution requires both a note and a recorded closure test result: "it
    # works now" without saying how it was checked is not evidence (CRM09).
    resolution_note = models.TextField(blank=True, default="")
    closure_test_result = models.TextField(blank=True, default="")
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    closed_at = models.DateTimeField(null=True, blank=True)

    # Counted rather than derived: reopening preserves prior resolution history,
    # and repeated reopening is itself a reported signal (CRM11).
    reopen_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "support_ticket"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(state=TicketState.RESOLVED)
                | (~models.Q(resolution_note="") & ~models.Q(closure_test_result="")),
                name="ck_ticket_resolution_requires_evidence",
            ),
            models.CheckConstraint(
                condition=~models.Q(
                    state__in=[
                        TicketState.WAITING_CUSTOMER,
                        TicketState.WAITING_INTERNAL,
                    ]
                )
                | ~models.Q(waiting_reason=""),
                name="ck_ticket_waiting_requires_reason",
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "state", "priority"]),
            models.Index(fields=["workspace", "owner", "state"]),
            models.Index(fields=["workspace", "client", "state"]),
            models.Index(
                fields=["workspace", "waiting_review_at"],
                condition=models.Q(
                    state__in=["waiting_customer", "waiting_internal"],
                    deleted_at__isnull=True,
                ),
                name="idx_ticket_waiting_review",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_open(self) -> bool:
        return self.state in OPEN_TICKET_STATES


class CommentVisibility(models.TextChoices):
    """Who may read a comment.

    INTERNAL is the default and must stay the default. A comment becomes
    client-visible only when somebody chooses that explicitly, because the cost
    of the two mistakes is wildly asymmetric: an internal note the client never
    sees is an inconvenience; an internal note the client does see can end the
    relationship.
    """

    INTERNAL = "internal", "Internal only"
    CLIENT_VISIBLE = "client_visible", "Visible to the client"


class TicketComment(models.Model):
    """Append-only ticket correspondence.

    Comments are not edited. A correction appends a new comment linked to the
    one it corrects, so the record of what was said, and when, survives
    (Journey D).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "identity.Workspace", on_delete=models.CASCADE, related_name="ticket_comments"
    )
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    body = models.TextField()
    visibility = models.CharField(
        max_length=20,
        choices=CommentVisibility.choices,
        default=CommentVisibility.INTERNAL,
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    corrects = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="corrections"
    )

    class Meta:
        db_table = "support_ticket_comment"
        indexes = [
            models.Index(fields=["workspace", "ticket", "created_at"]),
            models.Index(fields=["workspace", "visibility"]),
        ]

    def __str__(self) -> str:
        return f"{self.visibility} comment on {self.ticket_id}"


class TicketStateHistory(models.Model):
    """Append-only record of ticket state movement.

    Reopening returns a ticket to triage; without this table the earlier
    resolution and its evidence would be indistinguishable from never having
    been resolved (CRM09).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "identity.Workspace",
        on_delete=models.CASCADE,
        related_name="ticket_state_history",
    )
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="state_history")
    from_state = models.CharField(max_length=20, blank=True, default="")
    to_state = models.CharField(max_length=20)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    changed_at = models.DateTimeField(default=timezone.now, editable=False)
    reason = models.TextField(blank=True, default="")
    # Preserved from the resolution this transition superseded, so reopening
    # does not erase what was previously claimed to be fixed.
    superseded_resolution = models.TextField(blank=True, default="")

    class Meta:
        db_table = "support_ticket_state_history"
        indexes = [models.Index(fields=["workspace", "ticket", "changed_at"])]

    def __str__(self) -> str:
        return f"{self.from_state or '(new)'} -> {self.to_state}"
