"""Outbox, durable jobs, rules and notifications (CRM06, section 5.1, 8).

The chain is deliberately boring:

    business transaction  -> entity change + audit event + OutboxEvent (atomic)
    dispatcher            -> committed OutboxEvent  -> Job (unique key)
    worker                -> lease Job -> re-evaluate state -> act -> complete

Nothing relies on an in-memory callback after commit, which is lost if the
process exits between the commit and the callback (section 3.2). PostgreSQL is
the queue; there is no broker in Release 1 (ADR002).
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from modules.common.models import TimestampedModel, WorkspaceScopedModel


class OutboxEvent(models.Model):
    """A committed business fact awaiting dispatch.

    Written in the same transaction as the change it describes. If that
    transaction rolls back, the event disappears with it -- so an event can
    never describe something that did not happen.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "identity.Workspace", on_delete=models.CASCADE, related_name="outbox_events"
    )
    event_type = models.CharField(max_length=64)
    aggregate_type = models.CharField(max_length=64)
    aggregate_id = models.UUIDField()
    aggregate_version = models.PositiveIntegerField(default=1)
    # Minimal payload. The worker re-reads current state rather than trusting a
    # snapshot that may be stale by the time it runs.
    payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, editable=False)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    # Chain controls (section 8.3): a generated event must not re-enter the same
    # rule version within its own causal chain.
    correlation_id = models.UUIDField(default=uuid.uuid4)
    causation_id = models.UUIDField(null=True, blank=True)
    chain_depth = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "automation_outbox_event"
        indexes = [
            models.Index(
                fields=["occurred_at"],
                condition=models.Q(dispatched_at__isnull=True),
                name="idx_outbox_pending",
            ),
            models.Index(fields=["workspace", "aggregate_type", "aggregate_id"]),
            models.Index(fields=["correlation_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} for {self.aggregate_type}:{self.aggregate_id}"


class RuleTemplate(models.TextChoices):
    """The approved templates for Release 1 (SVX-PRD-001 section 5.2).

    Release 1 configures rules through forms over these templates. A general
    visual workflow builder is deferred; rule authors cannot supply code,
    SQL, URLs or unbounded regular expressions (section 8.1).
    """

    UNASSIGNED_LEAD = "unassigned_lead", "A01 unassigned lead"
    MISSING_NEXT_ACTION = "missing_next_action", "A02 opportunity without next action"
    OVERDUE_FOLLOWUP = "overdue_followup", "A03 overdue follow-up"
    STALE_DEAL = "stale_deal", "A04 no activity in an open deal"
    DEAL_WON = "deal_won", "A05 deal won"
    MILESTONE_OVERDUE = "milestone_overdue", "A06 milestone overdue or blocked"
    CRITICAL_TICKET = "critical_ticket", "A07 critical ticket created"
    REPEATED_RESCHEDULE = "repeated_reschedule", "A08 repeated rescheduling"


class RuleDefinition(WorkspaceScopedModel):
    """One configured rule, at one version.

    Editing a rule creates a new version rather than mutating the old one.
    Queued jobs record the version that scheduled them and are cancelled if it
    is no longer current, so an edit cannot silently change the meaning of work
    already in flight.
    """

    template = models.CharField(max_length=40, choices=RuleTemplate.choices)
    name = models.CharField(max_length=200)
    rule_version = models.PositiveIntegerField(default=1)
    enabled = models.BooleanField(default=False)
    trigger = models.CharField(max_length=64)
    # Validated against the Pydantic schemas in modules.automation.schemas
    # before being stored. The database holds only already-validated shapes.
    conditions = models.JSONField(default=list, blank=True)
    actions = models.JSONField(default=list, blank=True)
    delay = models.JSONField(default=dict, blank=True)
    limits = models.JSONField(default=dict, blank=True)
    explanation = models.TextField(
        blank=True,
        default="",
        help_text="Why this rule exists, shown with every alert it raises.",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    paused_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "automation_rule_definition"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "template", "rule_version"],
                name="uniq_rule_template_version",
            )
        ]
        indexes = [
            models.Index(fields=["workspace", "trigger", "enabled"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} v{self.rule_version}"

    @property
    def is_runnable(self) -> bool:
        return self.enabled and self.paused_at is None and self.deleted_at is None


class JobState(models.TextChoices):
    PENDING = "pending", "Pending"
    LEASED = "leased", "Leased by a worker"
    SUCCEEDED = "succeeded", "Succeeded"
    # Terminal, but reviewable and retryable by an operator.
    FAILED = "failed", "Failed"
    # Superseded by a business change before it ran (reply, closure,
    # reassignment, reschedule).
    CANCELLED = "cancelled", "Cancelled"


class Job(models.Model):
    """A unit of scheduled work.

    Claimed with ``SELECT ... FOR UPDATE SKIP LOCKED`` in a short transaction
    and stamped with a random lease token. A completion update must match the
    current token, so a worker that stalled past its lease cannot overwrite the
    result of the worker that took over (section 8.2, RD04).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "identity.Workspace", on_delete=models.CASCADE, related_name="jobs"
    )
    job_type = models.CharField(max_length=64)
    # Unique per (workspace, event, rule version, action): the dispatcher can
    # run twice without producing two business actions (CRM06 acceptance).
    dedupe_key = models.CharField(max_length=200)
    source_event = models.ForeignKey(
        OutboxEvent, null=True, blank=True, on_delete=models.SET_NULL, related_name="jobs"
    )
    rule = models.ForeignKey(
        RuleDefinition, null=True, blank=True, on_delete=models.SET_NULL, related_name="jobs"
    )
    rule_version = models.PositiveIntegerField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)

    due_at = models.DateTimeField()
    state = models.CharField(max_length=16, choices=JobState.choices, default=JobState.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    lease_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    # A short, safe description. Never a raw provider response, which can carry
    # recipient data.
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.CharField(max_length=200, blank=True, default="")
    correlation_id = models.UUIDField(null=True, blank=True)
    chain_depth = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "automation_job"
        constraints = [
            models.UniqueConstraint(fields=["workspace", "dedupe_key"], name="uniq_job_dedupe_key")
        ]
        indexes = [
            # The claim query: due, runnable jobs in due order.
            models.Index(
                fields=["due_at"],
                condition=models.Q(state__in=["pending", "leased"]),
                name="idx_job_runnable",
            ),
            models.Index(fields=["workspace", "state", "due_at"]),
            models.Index(fields=["state", "lease_expires_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.job_type} due {self.due_at:%Y-%m-%d %H:%M}"


class RuleExecution(models.Model):
    """Proof that one rule action ran once for one event.

    Inserted in the same transaction as the action it records. The unique key
    is what makes an at-least-once queue produce exactly-once *effects* for
    internal database actions (section 8.2).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "identity.Workspace", on_delete=models.CASCADE, related_name="rule_executions"
    )
    rule = models.ForeignKey(
        RuleDefinition, null=True, on_delete=models.SET_NULL, related_name="executions"
    )
    rule_version = models.PositiveIntegerField()
    event = models.ForeignKey(
        OutboxEvent, null=True, on_delete=models.SET_NULL, related_name="executions"
    )
    event_key = models.CharField(max_length=200)
    action_type = models.CharField(max_length=64)
    executed_at = models.DateTimeField(default=timezone.now, editable=False)
    result = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "automation_rule_execution"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "event_key", "rule_version", "action_type"],
                name="uniq_rule_execution",
            )
        ]
        indexes = [models.Index(fields=["workspace", "-executed_at"])]

    def __str__(self) -> str:
        return f"{self.action_type} for {self.event_key}"


class NotificationChannel(models.TextChoices):
    IN_APP = "in_app", "In-app alert"
    # External channels stay disabled until the connector and the authority to
    # send exist (CRM06).
    EMAIL = "email", "Email"


class NotificationState(models.TextChoices):
    PENDING = "pending", "Pending"
    DELIVERED = "delivered", "Shown to the recipient"
    ACKNOWLEDGED = "acknowledged", "Acknowledged"
    RESOLVED = "resolved", "Resolved"
    FAILED = "failed", "Failed"


class Notification(WorkspaceScopedModel):
    """An alert raised for a person.

    A queued alert is not proof that a recipient received it (rule catalogue
    preamble), so the state machine distinguishes pending, delivered,
    acknowledged and failed rather than collapsing them into "sent".
    """

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    channel = models.CharField(
        max_length=16, choices=NotificationChannel.choices, default=NotificationChannel.IN_APP
    )
    state = models.CharField(
        max_length=16, choices=NotificationState.choices, default=NotificationState.PENDING
    )
    # One unresolved alert per cause, so a nightly rule does not pile up
    # duplicates for the same neglected lead (rule A01).
    dedupe_key = models.CharField(max_length=200)
    title = models.CharField(max_length=300)
    body = models.TextField(blank=True, default="")
    # Every alert explains why it appeared and opens the underlying record
    # (SVX-PRD-001 section 7.2).
    cause = models.TextField(blank=True, default="")
    entity_type = models.CharField(max_length=64, blank=True, default="")
    entity_id = models.UUIDField(null=True, blank=True)
    rule = models.ForeignKey(
        RuleDefinition, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    resolution_note = models.TextField(blank=True, default="")

    class Meta:
        db_table = "automation_notification"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "dedupe_key"],
                condition=models.Q(resolved_at__isnull=True),
                name="uniq_unresolved_notification",
            )
        ]
        indexes = [
            models.Index(fields=["workspace", "recipient", "state", "-created_at"]),
            models.Index(fields=["workspace", "-created_at"]),
        ]

    def __str__(self) -> str:
        return self.title


class SchedulerHeartbeat(TimestampedModel):
    """Liveness evidence for the scheduler and worker (section 12.1).

    Monitored so that a silently dead worker is visible. The absence of alerts
    is otherwise indistinguishable from a worker that stopped running.
    """

    component = models.CharField(max_length=40, unique=True)
    last_beat_at = models.DateTimeField()
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "automation_scheduler_heartbeat"
