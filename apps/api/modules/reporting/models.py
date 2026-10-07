"""Cash receipts (CRM11).

This is an **operational register, not an accounting ledger** and not a bank
reconciliation. It exists so the CEO dashboard can separate three things the
business constantly conflates:

    open pipeline value   — what we might win
    won contract value    — what we agreed
    cash received         — what actually arrived

A closed-won deal does **not** create a receipt. Nothing here is implicit:
somebody with the authority enters the amount, the date and an evidence
reference, and that entry is audited.

Corrections append an adjustment rather than editing the original. A register
whose past can be quietly rewritten is not evidence of anything.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from modules.common.models import (
    MONEY_DECIMAL_PLACES,
    MONEY_MAX_DIGITS,
    WorkspaceScopedModel,
)


class CashReceipt(WorkspaceScopedModel):
    client = models.ForeignKey("crm.Client", on_delete=models.PROTECT, related_name="receipts")
    opportunity = models.ForeignKey(
        "crm.Opportunity",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="receipts",
    )
    # Never a float. Currency is mandatory here, unlike an opportunity amount:
    # money that has actually arrived always arrived in some currency.
    amount = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    currency = models.CharField(max_length=3)
    received_at = models.DateField()
    # A bank reference, invoice number or statement line. Required: an
    # unevidenced figure is a claim, not a record.
    evidence_reference = models.CharField(max_length=200)
    note = models.TextField(blank=True, default="")

    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    # An adjustment points at the receipt it corrects. The original stays.
    adjusts = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="adjustments",
    )
    adjustment_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "reporting_cash_receipt"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(currency=""),
                name="ck_receipt_requires_currency",
            ),
            models.CheckConstraint(
                condition=~models.Q(evidence_reference=""),
                name="ck_receipt_requires_evidence",
            ),
            models.CheckConstraint(
                # An adjustment may be negative; an original receipt may not.
                condition=models.Q(amount__gt=0) | models.Q(adjusts__isnull=False),
                name="ck_receipt_positive_unless_adjustment",
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "received_at"]),
            models.Index(fields=["workspace", "client", "received_at"]),
            models.Index(fields=["workspace", "currency", "received_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.currency} {self.amount} on {self.received_at}"


class ExceptionKind(models.TextChoices):
    """Things a manager should look at (CRM10).

    Every one of these is a prompt for a conversation. None of them is a finding
    of misconduct, and the system must never present them as one.
    """

    MISSED_DEADLINE = "missed_deadline", "Missed deadline"
    REPEATED_RESCHEDULE = "repeated_reschedule", "Repeated rescheduling"
    NEGLECTED_LEAD = "neglected_lead", "Lead with no owner or no contact"
    NO_NEXT_ACTION = "no_next_action", "Open deal with no next action"
    HANDOVER_INCOMPLETE = "handover_incomplete", "Handover missing information"
    DELIVERY_BLOCKED = "delivery_blocked", "Milestone blocked or overdue"
    TICKET_REOPENED = "ticket_reopened", "Ticket reopened after resolution"


class ExceptionState(models.TextChoices):
    OPEN = "open", "Open"
    UNDER_REVIEW = "under_review", "Under review"
    # The employee disagrees and has said why. A disputed exception stays
    # visible; it is not silently dropped.
    DISPUTED = "disputed", "Disputed by the employee"
    RESOLVED = "resolved", "Resolved"
    DISMISSED = "dismissed", "Dismissed"


class WorkException(WorkspaceScopedModel):
    """A reviewable exception raised for management attention (CRM10).

    The rules the brief sets for this table are strict and deliberate:

    * The system does not infer dishonesty from inactivity.
    * No exception triggers an automatic disciplinary action.
    * The employee can see it, explain it and dispute it.
    * A manager records a decision, with a reason.

    Screen time, click counts and call volume are **not** stored here and must
    not be added. They are not measures of revenue contribution.
    """

    kind = models.CharField(max_length=32, choices=ExceptionKind.choices)
    state = models.CharField(
        max_length=16, choices=ExceptionState.choices, default=ExceptionState.OPEN
    )
    # One open exception per cause, so a nightly sweep does not accumulate
    # duplicates about the same overdue task.
    dedupe_key = models.CharField(max_length=200)

    subject = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="exceptions",
        help_text="The person whose work this concerns, where there is one.",
    )
    entity_type = models.CharField(max_length=64, blank=True, default="")
    entity_id = models.UUIDField(null=True, blank=True)

    summary = models.CharField(max_length=300)
    # The observable facts that produced this exception. Shown to the employee
    # so a dispute is about evidence rather than about a verdict.
    detail = models.JSONField(default=dict, blank=True)

    employee_explanation = models.TextField(blank=True, default="")
    employee_responded_at = models.DateTimeField(null=True, blank=True)

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_exceptions",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_decision = models.TextField(blank=True, default="")

    class Meta:
        db_table = "reporting_work_exception"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "dedupe_key"],
                condition=models.Q(state__in=["open", "under_review", "disputed"]),
                name="uniq_open_exception",
            ),
            models.CheckConstraint(
                condition=~models.Q(state__in=["resolved", "dismissed"])
                | ~models.Q(review_decision=""),
                name="ck_exception_closure_requires_decision",
            ),
        ]
        indexes = [
            models.Index(fields=["workspace", "state", "-created_at"]),
            models.Index(fields=["workspace", "subject", "state"]),
            models.Index(fields=["workspace", "kind", "state"]),
        ]

    def __str__(self) -> str:
        return self.summary


class ReportSnapshot(models.Model):
    """A saved figure, with the filter that produced it.

    Every figure on a dashboard must expose its filter, time period and
    underlying records (CRM11). Storing the parameters alongside the value is
    what makes a number from last month reproducible rather than merely
    plausible.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "identity.Workspace", on_delete=models.CASCADE, related_name="report_snapshots"
    )
    report_key = models.CharField(max_length=64)
    parameters = models.JSONField(default=dict, blank=True)
    values = models.JSONField(default=dict, blank=True)
    generated_at = models.DateTimeField(default=timezone.now, editable=False)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "reporting_snapshot"
        indexes = [models.Index(fields=["workspace", "report_key", "-generated_at"])]

    def __str__(self) -> str:
        return f"{self.report_key} at {self.generated_at:%Y-%m-%d %H:%M}"
