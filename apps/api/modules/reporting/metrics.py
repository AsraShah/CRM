"""Reporting calculations (CRM11).

Three rules govern everything in this module.

**1. Three money figures, never merged.**
Open pipeline value, won contract value and recorded cash receipts are separate
measures of separate things. A closed-won deal does not increase collected
revenue. Presenting them as one number is the single most misleading thing a
CRM dashboard can do.

**2. Currencies are never combined without an explicit basis.**
Every monetary figure is returned as a mapping of currency to amount. There is
no "total". Summing USD and PKR requires a conversion rate somebody has agreed,
and nobody has.

**3. Every figure exposes its filter, period and underlying records.**
Each result carries the parameters that produced it and a query the caller can
follow to the rows behind it.

Deals whose value is unknown are counted separately, never as zero.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Count, Q, Sum
from django.utils import timezone

from modules.common.tenancy import current_workspace_id
from modules.crm.models import (
    OPEN_STAGES,
    Activity,
    Client,
    ClientStatus,
    Lead,
    LeadStatus,
    Opportunity,
    OpportunityStage,
)
from modules.identity.models import Membership, Role
from modules.reporting.models import CashReceipt
from modules.support.models import OPEN_TICKET_STATES, Ticket, TicketState
from modules.work.models import Milestone, MilestoneStatus, Task, TaskStatus


@dataclass(slots=True)
class MoneyByCurrency:
    """An amount per currency, plus a count of records with unknown value.

    ``unknown_count`` exists so a reader can tell "no pipeline" from "pipeline
    we have not valued yet". Those are very different situations.
    """

    amounts: dict[str, str] = field(default_factory=dict)
    record_count: int = 0
    unknown_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _money_by_currency(queryset, amount_field: str = "amount") -> MoneyByCurrency:
    total = queryset.count()
    unknown = queryset.filter(Q(**{f"{amount_field}__isnull": True}) | Q(currency="")).count()

    rows = (
        queryset.exclude(**{f"{amount_field}__isnull": True})
        .exclude(currency="")
        .values("currency")
        .annotate(total=Sum(amount_field))
        .order_by("currency")
    )
    return MoneyByCurrency(
        amounts={row["currency"]: str(row["total"] or Decimal("0")) for row in rows},
        record_count=total,
        unknown_count=unknown,
    )


def _visible_scope(membership: Membership) -> dict[str, Any]:
    """Restrict a report to what this actor may see.

    Reports exclude inaccessible records (CRM11 acceptance). A representative's
    dashboard shows their own numbers; it does not quietly aggregate the
    workspace and present it as theirs.
    """
    if membership.role in {Role.OWNER, Role.ADMIN, Role.SALES_MANAGER}:
        return {}
    return {"owner_id": membership.user_id}


# ---------------------------------------------------------------------------
# CEO overview
# ---------------------------------------------------------------------------


def overview(
    *,
    membership: Membership,
    period_start: date | None = None,
    period_end: date | None = None,
    currency: str | None = None,
) -> dict[str, Any]:
    """The CEO dashboard (CRM11).

    Returns the three money measures separately, alongside the operational
    exceptions that explain them.
    """
    today = timezone.now().date()
    period_start = period_start or today.replace(day=1)
    period_end = period_end or today
    scope = _visible_scope(membership)

    # The same definitions the drill-down uses, so each figure reconciles to
    # the records behind it.
    period = {"membership": membership, "period_start": period_start, "period_end": period_end}
    open_pipeline = _money_by_currency(measure_queryset("open_pipeline_value", **period))
    won_in_period = _money_by_currency(measure_queryset("won_contract_value", **period))

    # Receipts are entered by hand against evidence. Winning a deal creates
    # nothing here.
    receipts = measure_queryset("cash_received", **period)
    if currency:
        receipts = receipts.filter(currency=currency.upper())
    receipt_rows = receipts.values("currency").annotate(total=Sum("amount")).order_by("currency")

    return {
        "period": {"start": period_start.isoformat(), "end": period_end.isoformat()},
        "scope": "workspace" if not scope else "own_records",
        # Deliberately three fields, not one. See the module docstring.
        "open_pipeline_value": open_pipeline.as_dict(),
        "won_contract_value": won_in_period.as_dict(),
        "cash_received": {
            "amounts": {row["currency"]: str(row["total"] or Decimal("0")) for row in receipt_rows},
            "record_count": receipts.count(),
            "note": (
                "Manually recorded receipts against evidence references. This is "
                "an operational register, not an accounting ledger or a bank "
                "reconciliation."
            ),
        },
        "attention": attention_counts(membership=membership),
        "basis": {
            "currencies_combined": False,
            "unknown_values_counted_as_zero": False,
            "filters": {"currency": currency, **{k: str(v) for k, v in scope.items()}},
        },
    }


#: Every attention measure, in display order. Each is defined once, as a
#: queryset, so the count on the dashboard and the records behind it come from
#: the same definition and cannot disagree.
ATTENTION_MEASURES = (
    "leads_without_owner",
    "open_deals_without_next_action",
    "overdue_tasks",
    "handovers_awaiting_acceptance",
    "blocked_milestones",
    "overdue_milestones",
    "open_critical_tickets",
    "reopened_tickets",
)

#: Money measures that can be drilled into, beside the attention measures.
MONEY_MEASURES = ("open_pipeline_value", "won_contract_value", "cash_received")


def measure_queryset(
    measure: str,
    *,
    membership: Membership,
    period_start: date | None = None,
    period_end: date | None = None,
):
    """The records behind one dashboard figure (CRM11).

    Raises KeyError for an unknown measure, which the view turns into a 400.
    """
    workspace_id = current_workspace_id()
    now = timezone.now()
    today = now.date()
    period_start = period_start or today.replace(day=1)
    period_end = period_end or today
    scope = _visible_scope(membership)
    live = {"workspace_id": workspace_id, "deleted_at__isnull": True}
    deals = Opportunity.objects.filter(**live, **scope)

    if measure == "open_pipeline_value":
        return deals.filter(stage__in=OPEN_STAGES)
    if measure == "won_contract_value":
        return deals.filter(
            stage=OpportunityStage.WON,
            closed_at__date__gte=period_start,
            closed_at__date__lte=period_end,
        )
    if measure == "cash_received":
        return CashReceipt.objects.filter(
            **live, received_at__gte=period_start, received_at__lte=period_end
        )
    if measure == "leads_without_owner":
        return Lead.objects.filter(**live, owner__isnull=True).exclude(
            status=LeadStatus.DISQUALIFIED
        )
    if measure == "open_deals_without_next_action":
        return deals.filter(stage__in=OPEN_STAGES, next_action_at__isnull=True)
    if measure == "overdue_tasks":
        return Task.objects.filter(
            **live,
            status=TaskStatus.OPEN,
            due_at__lt=now,
            **({"owner_id": membership.user_id} if scope else {}),
        )
    if measure == "handovers_awaiting_acceptance":
        return Client.objects.filter(**live, status=ClientStatus.PENDING_HANDOVER)
    if measure == "blocked_milestones":
        return Milestone.objects.filter(**live, status=MilestoneStatus.BLOCKED)
    if measure == "overdue_milestones":
        return Milestone.objects.filter(**live, due_on__lt=today).exclude(
            status__in=[MilestoneStatus.ACCEPTED, MilestoneStatus.CANCELLED]
        )
    if measure == "open_critical_tickets":
        return Ticket.objects.filter(**live, priority="critical", state__in=OPEN_TICKET_STATES)
    if measure == "reopened_tickets":
        return Ticket.objects.filter(**live, reopen_count__gt=0)
    raise KeyError(measure)


def attention_counts(*, membership: Membership) -> dict[str, int]:
    """Operational exceptions worth a manager's attention.

    Counts only; ``measure_records`` returns the rows behind each, because a
    number without its rows cannot be acted on.
    """
    return {
        measure: measure_queryset(measure, membership=membership).count()
        for measure in ATTENTION_MEASURES
    }


def _describe(record) -> dict[str, Any]:
    """One row of a drill-down: what it is, enough to recognise it, and where."""
    if isinstance(record, Opportunity):
        return {
            "type": "opportunity",
            "id": str(record.id),
            "label": f"{record.service} · {record.contact.display_name}",
            "detail": (
                f"{record.currency} {record.amount}"
                if record.amount is not None
                else "Value not yet known"
            ),
        }
    if isinstance(record, CashReceipt):
        return {
            "type": "receipt",
            "id": str(record.id),
            "label": f"{record.client.display_name} · {record.evidence_reference}",
            "detail": f"{record.currency} {record.amount} on {record.received_at.isoformat()}",
        }
    if isinstance(record, Lead):
        return {
            "type": "lead",
            "id": str(record.id),
            "label": record.contact.display_name,
            "detail": f"{record.status} · source {record.source}",
        }
    if isinstance(record, Task):
        return {
            "type": "task",
            "id": str(record.id),
            "label": record.title,
            "detail": (
                f"due {record.due_at.isoformat()} · {getattr(record.owner, 'email', 'unassigned')}"
            ),
        }
    if isinstance(record, Client):
        return {
            "type": "client",
            "id": str(record.id),
            "label": record.display_name,
            "detail": "awaiting handover acceptance",
        }
    if isinstance(record, Milestone):
        return {
            "type": "milestone",
            "id": str(record.id),
            "label": f"{record.project.name}: {record.name}",
            "detail": record.blocked_reason or f"due {record.due_on}",
        }
    if isinstance(record, Ticket):
        return {
            "type": "ticket",
            "id": str(record.id),
            "label": record.title,
            "detail": f"{record.state} · {record.priority} · reopened {record.reopen_count}×",
        }
    return {"type": "record", "id": str(record.pk), "label": str(record), "detail": ""}


#: Related rows each drill-down label needs, loaded in one query.
_RELATED = {
    Opportunity: ("contact",),
    CashReceipt: ("client",),
    Lead: ("contact",),
    Task: ("owner",),
    Milestone: ("project",),
}


def measure_records(
    measure: str,
    *,
    membership: Membership,
    period_start: date | None = None,
    period_end: date | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """The records behind a figure, with the count that reconciles to it."""
    queryset = measure_queryset(
        measure, membership=membership, period_start=period_start, period_end=period_end
    )
    related = _RELATED.get(queryset.model, ())
    if related:
        queryset = queryset.select_related(*related)
    total = queryset.count()
    rows = [_describe(record) for record in queryset.order_by("-created_at")[:limit]]
    return {
        "measure": measure,
        "count": total,
        "shown": len(rows),
        "records": rows,
    }


def conversion_by_source(
    *, membership: Membership, period_start: date, period_end: date
) -> dict[str, Any]:
    """Where deals come from, and how many close.

    Rates are reported with their numerator and denominator, not as a bare
    percentage. "40%" over five deals is noise; the reader needs to see that.
    """
    workspace_id = current_workspace_id()
    rows = (
        Lead.objects.filter(
            workspace_id=workspace_id,
            deleted_at__isnull=True,
            created_at__date__gte=period_start,
            created_at__date__lte=period_end,
        )
        .values("source")
        .annotate(
            leads=Count("id"),
            qualified=Count("id", filter=Q(status=LeadStatus.QUALIFIED)),
            disqualified=Count("id", filter=Q(status=LeadStatus.DISQUALIFIED)),
        )
        .order_by("-leads")
    )

    results = []
    for row in rows:
        won = Opportunity.objects.filter(
            workspace_id=workspace_id,
            deleted_at__isnull=True,
            lead__source=row["source"],
            stage=OpportunityStage.WON,
            closed_at__date__gte=period_start,
            closed_at__date__lte=period_end,
        ).count()
        results.append(
            {
                "source": row["source"],
                "leads": row["leads"],
                "qualified": row["qualified"],
                "disqualified": row["disqualified"],
                "won": won,
                # Both parts shown so a small sample is visibly small.
                "won_per_lead": f"{won}/{row['leads']}",
            }
        )

    return {
        "period": {"start": period_start.isoformat(), "end": period_end.isoformat()},
        "rows": results,
        "caveat": (
            "Counts over a short period or a small sample are directional only. "
            "Lead volume, staffing and campaigns all move these numbers."
        ),
    }


def stage_aging(*, membership: Membership) -> dict[str, Any]:
    """How long open deals have sat where they are."""
    workspace_id = current_workspace_id()
    now = timezone.now()
    scope = _visible_scope(membership)

    buckets: dict[str, dict[str, int]] = {}
    for stage in sorted(OPEN_STAGES):
        deals = Opportunity.objects.filter(
            workspace_id=workspace_id,
            deleted_at__isnull=True,
            stage=stage,
            **scope,
        )
        buckets[stage] = {
            "total": deals.count(),
            "over_7_days": deals.filter(stage_entered_at__lt=now - timedelta(days=7)).count(),
            "over_30_days": deals.filter(stage_entered_at__lt=now - timedelta(days=30)).count(),
            # Age cannot be computed without an entry timestamp; say so rather
            # than defaulting it to zero days.
            "age_unknown": deals.filter(stage_entered_at__isnull=True).count(),
        }
    return {"stages": buckets}


def activity_evidence_split(
    *, membership: Membership, period_start: date, period_end: date
) -> dict[str, Any]:
    """Self-reported versus provider-confirmed activity (CRM10).

    Management can see the difference. That is the whole purpose: a gap between
    what was typed in and what a provider confirmed is a prompt to ask, not a
    finding. Until an email connector exists in Release 2, everything is
    self-reported and the split is expected to be total.
    """
    workspace_id = current_workspace_id()
    rows = (
        Activity.objects.filter(
            workspace_id=workspace_id,
            deleted_at__isnull=True,
            occurred_at__date__gte=period_start,
            occurred_at__date__lte=period_end,
        )
        .values("evidence_type")
        .annotate(total=Count("id"))
    )
    counts = {row["evidence_type"]: row["total"] for row in rows}
    return {
        "period": {"start": period_start.isoformat(), "end": period_end.isoformat()},
        "self_reported": counts.get("self_reported", 0),
        "provider_confirmed": counts.get("provider_confirmed", 0),
        "interpretation": (
            "A self-reported activity is a colleague's account of what happened. "
            "It is not evidence that it did, and a difference between the two "
            "counts is a question to ask, not a conclusion to draw."
        ),
    }


def delivery_health(*, membership: Membership) -> dict[str, Any]:
    """Blockers and reopened work (CRM11)."""
    workspace_id = current_workspace_id()

    blocked = Milestone.objects.filter(
        workspace_id=workspace_id,
        deleted_at__isnull=True,
        status=MilestoneStatus.BLOCKED,
    ).select_related("project", "blocked_next_owner")

    return {
        "blocked_milestones": [
            {
                "id": str(m.id),
                "name": m.name,
                "project": m.project.name,
                "reason": m.blocked_reason,
                "next_owner": str(m.blocked_next_owner_id) if m.blocked_next_owner_id else None,
            }
            for m in blocked[:50]
        ],
        "dependency_overrides": Milestone.objects.filter(
            workspace_id=workspace_id,
            deleted_at__isnull=True,
            dependency_override_by__isnull=False,
        ).count(),
        "reopened_tickets": Ticket.objects.filter(
            workspace_id=workspace_id, deleted_at__isnull=True, reopen_count__gt=0
        ).count(),
        "tickets_waiting_past_review": Ticket.objects.filter(
            workspace_id=workspace_id,
            deleted_at__isnull=True,
            state__in=[TicketState.WAITING_CUSTOMER, TicketState.WAITING_INTERNAL],
            waiting_review_at__lt=timezone.now(),
        ).count(),
    }


def pilot_measures(*, membership: Membership) -> dict[str, Any]:
    """The pilot decision thresholds from SVX-PRD-001 section 8.

    These are **proposed validation thresholds**, not performance results, and
    management may revise them at G0. Each measure is returned with its
    numerator and denominator so a reader can inspect the raw counts rather than
    trusting a percentage.
    """
    workspace_id = current_workspace_id()

    active_leads = Lead.objects.filter(workspace_id=workspace_id, deleted_at__isnull=True).exclude(
        status=LeadStatus.DISQUALIFIED
    )
    owned_leads = active_leads.filter(owner__isnull=False)

    open_deals = Opportunity.objects.filter(
        workspace_id=workspace_id, deleted_at__isnull=True, stage__in=OPEN_STAGES
    )
    with_next_action = open_deals.filter(next_action_at__gt=timezone.now())

    won_deals = Opportunity.objects.filter(
        workspace_id=workspace_id, deleted_at__isnull=True, stage=OpportunityStage.WON
    )
    complete_handovers = won_deals.filter(delivery_owner__isnull=False).exclude(
        accepted_scope_evidence=""
    )

    return {
        "ownership_coverage": _ratio(
            owned_leads.count(), active_leads.count(), threshold="at least 95%"
        ),
        "next_action_coverage": _ratio(
            with_next_action.count(), open_deals.count(), threshold="at least 95%"
        ),
        "handover_completeness": _ratio(
            complete_handovers.count(),
            won_deals.count(),
            threshold="100% before delivery begins",
        ),
        "note": (
            "Proposed validation thresholds from SVX-PRD-001 section 8. They are "
            "not performance results, and they require a measured baseline from "
            "a representative week before they mean anything."
        ),
    }


def _ratio(numerator: int, denominator: int, *, threshold: str) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        # Null rather than 0% or 100% when there is nothing to measure: an
        # empty workspace has no coverage figure, good or bad.
        "percentage": round(100 * numerator / denominator, 1) if denominator else None,
        "proposed_threshold": threshold,
    }
