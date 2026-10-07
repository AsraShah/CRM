"""AI budget reservations and drafts (CRM12, SVX-TECH-001 section 10.3).

The budget is enforced **here**, by the application, before a request is sent.
A provider dashboard alert is secondary: it arrives after the money is spent.

The reservation pattern:

    1. Estimate the maximum cost this request could incur.
    2. Reserve that amount in a locked budget row. If the reservation would
       exceed the remaining monthly ceiling, deny.
    3. Dispatch.
    4. Settle the actual cost afterwards, releasing the difference.

Step 2 uses a row lock precisely so that concurrent requests cannot each read
the same remaining budget and both decide there is room. An uncertain or
timed-out request keeps its reservation until somebody reconciles it -- releasing
it optimistically would let a silent failure spend the budget twice.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from modules.common.models import WorkspaceScopedModel


class BudgetPeriod(models.Model):
    """One workspace's AI spend for one calendar month.

    Holds the authoritative running totals. Every reservation locks this row,
    which serialises budget decisions -- the correct trade at one concurrent
    request per workspace (section 10.3).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "identity.Workspace", on_delete=models.CASCADE, related_name="ai_budget_periods"
    )
    # First day of the month this period covers.
    period_start = models.DateField()
    ceiling_usd = models.DecimalField(max_digits=10, decimal_places=4)
    # Money promised to in-flight requests but not yet settled.
    reserved_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    # Money actually spent, from settled requests.
    settled_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    # An administrator kill switch, independent of the ceiling (section 10.3).
    disabled_at = models.DateTimeField(null=True, blank=True)
    disabled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    disabled_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "ai_budget_period"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "period_start"], name="uniq_ai_budget_period"
            )
        ]

    def __str__(self) -> str:
        return f"{self.workspace_id} {self.period_start}"

    @property
    def committed_usd(self):
        """Everything already promised: settled spend plus live reservations."""
        return self.settled_usd + self.reserved_usd

    @property
    def remaining_usd(self):
        return self.ceiling_usd - self.committed_usd

    @property
    def is_available(self) -> bool:
        return self.disabled_at is None and self.remaining_usd > 0


class ReservationState(models.TextChoices):
    RESERVED = "reserved", "Reserved, request in flight"
    SETTLED = "settled", "Settled with actual usage"
    RELEASED = "released", "Released without spend"
    # The outcome is genuinely unknown: a timeout, or a response we could not
    # read. The reservation is held, not released, until reconciled.
    UNCERTAIN = "uncertain", "Uncertain, awaiting reconciliation"


class AIUsageReservation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "identity.Workspace", on_delete=models.CASCADE, related_name="ai_reservations"
    )
    period = models.ForeignKey(BudgetPeriod, on_delete=models.CASCADE, related_name="reservations")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    # Binds a retry to its original reservation, so a repeated request does not
    # reserve the budget twice.
    request_key = models.CharField(max_length=128)
    purpose = models.CharField(max_length=32)

    reserved_usd = models.DecimalField(max_digits=10, decimal_places=4)
    estimated_input_tokens = models.PositiveIntegerField()
    max_output_tokens = models.PositiveIntegerField()

    actual_input_tokens = models.PositiveIntegerField(null=True, blank=True)
    actual_output_tokens = models.PositiveIntegerField(null=True, blank=True)
    actual_usd = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)

    state = models.CharField(
        max_length=16, choices=ReservationState.choices, default=ReservationState.RESERVED
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    settled_at = models.DateTimeField(null=True, blank=True)
    failure_note = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        db_table = "ai_usage_reservation"
        constraints = [
            models.UniqueConstraint(fields=["workspace", "request_key"], name="uniq_ai_request_key")
        ]
        indexes = [
            models.Index(fields=["workspace", "state", "-created_at"]),
            models.Index(fields=["period", "state"]),
        ]

    def __str__(self) -> str:
        return f"{self.purpose} {self.state} ({self.reserved_usd} USD)"


class DraftPurpose(models.TextChoices):
    """The only two things AI is permitted to do (section 10.1).

    Neither receives tools or write authority. Pipeline logic, assignment,
    reminders, reporting and employee alerts are deterministic code.
    """

    SUMMARISE_NOTES = "summarise_notes", "Summarise selected activity notes"
    DRAFT_FOLLOW_UP = "draft_follow_up", "Draft a follow-up message"


class AIDraft(WorkspaceScopedModel):
    """A generated draft, always subject to human review.

    The draft is never sent, and never applied to a record, by the system. The
    user reads it beside the source notes and copies, edits or rejects it.

    ``source_ids`` and ``input_versions`` are stored so the source view can be
    reproduced exactly as it was when the draft was made. A draft that cannot be
    compared against its sources is not reviewable, and schema validity alone
    does not establish factual accuracy.
    """

    purpose = models.CharField(max_length=32, choices=DraftPurpose.choices)
    reservation = models.OneToOneField(
        AIUsageReservation, null=True, on_delete=models.SET_NULL, related_name="draft"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )

    contact = models.ForeignKey(
        "crm.Contact", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    opportunity = models.ForeignKey(
        "crm.Opportunity",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="ai_drafts",
    )

    # Exactly which records were sent, and at which version.
    source_ids = models.JSONField(default=list, blank=True)
    input_versions = models.JSONField(default=dict, blank=True)
    prompt_version = models.CharField(max_length=32)
    model_name = models.CharField(max_length=64)

    # The validated structured response, or empty if validation rejected it.
    output = models.JSONField(default=dict, blank=True)
    # What the model said it was unsure about. Surfaced to the reviewer rather
    # than hidden, because a confident-sounding draft is the dangerous one.
    uncertainties = models.JSONField(default=list, blank=True)

    accepted_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default="")
    validation_error = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        db_table = "ai_draft"
        indexes = [
            models.Index(fields=["workspace", "requested_by", "-created_at"]),
            models.Index(fields=["workspace", "purpose", "-created_at"]),
        ]
