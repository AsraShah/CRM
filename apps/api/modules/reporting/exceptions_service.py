"""Exception review (CRM10).

The brief is unusually explicit about what this must *not* do, and those
constraints shape the whole module:

* The system shall not infer dishonesty from inactivity.
* It shall not impose automatic disciplinary action.
* Screen time, clicks and call counts are not measures of revenue contribution,
  and are not recorded here.
* An employee can see an exception, explain it and dispute it.
* A manager reviews and records a decision, with a reason.

So an exception is a **prompt for a conversation**, backed by the observable
facts that produced it. It has no severity, no score and no cumulative tally,
because any of those would turn a queue of questions into a performance
ranking.

Attendance, leave and onboarding checklists are Release 2. Payroll, covert
screenshots, keylogging, biometrics and automated sanctions are outside the
product baseline entirely and must never be added here.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from modules.common.audit import AuditAction, record_audit
from modules.common.exceptions import NotAuthorized, ValidationFailed
from modules.common.tenancy import current_workspace_id
from modules.crm.models import OPEN_STAGES, Client, ClientStatus, Lead, LeadStatus, Opportunity
from modules.identity.models import Membership, Role, User
from modules.identity.policy import has_permission
from modules.reporting.models import ExceptionKind, ExceptionState, WorkException
from modules.support.models import Ticket
from modules.work.models import Milestone, MilestoneStatus, Task, TaskStatus

#: A task rescheduled this many times in this window raises rule A08.
RESCHEDULE_THRESHOLD = 3
RESCHEDULE_WINDOW_DAYS = 7


def _require(membership: Membership, permission: str) -> None:
    if not has_permission(membership, permission):
        raise NotAuthorized(f"This role may not perform {permission}.")


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def sweep() -> dict[str, int]:
    """Detect current exceptions for the active workspace.

    Run from the scheduler tick. Idempotent: the partial unique index on
    ``dedupe_key`` means re-running does not accumulate duplicates about the
    same underlying fact.

    Note that nothing here is raised because somebody was *inactive*. Every
    detector points at a specific record in a specific state — a task past its
    deadline, a deal with nothing scheduled, a handover nobody accepted.
    """
    counts = dict.fromkeys(ExceptionKind.values, 0)
    now = timezone.now()
    workspace_id = current_workspace_id()

    # Overdue tasks. The fact is the deadline, not the person.
    for task in Task.objects.filter(
        workspace_id=workspace_id,
        deleted_at__isnull=True,
        status=TaskStatus.OPEN,
        due_at__lt=now - timedelta(hours=24),
    ).select_related("owner")[:200]:
        if _raise_exception(
            kind=ExceptionKind.MISSED_DEADLINE,
            dedupe_key=f"task:{task.id}:overdue",
            subject=task.owner,
            entity_type="work.Task",
            entity_id=task.id,
            summary=f"Task past its deadline: {task.title[:120]}",
            detail={
                "due_at": task.due_at.isoformat(),
                "hours_overdue": int((now - task.due_at).total_seconds() // 3600),
                "original_due_at": task.original_due_at.isoformat(),
            },
        ):
            counts[ExceptionKind.MISSED_DEADLINE] += 1

    # Repeated rescheduling (rule A08). Explicitly "a reviewable exception, not
    # a disciplinary decision".
    window_start = now - timedelta(days=RESCHEDULE_WINDOW_DAYS)
    for task in Task.objects.filter(
        workspace_id=workspace_id,
        deleted_at__isnull=True,
        reschedule_count__gte=RESCHEDULE_THRESHOLD,
        updated_at__gte=window_start,
    ).select_related("owner")[:200]:
        if _raise_exception(
            kind=ExceptionKind.REPEATED_RESCHEDULE,
            dedupe_key=f"task:{task.id}:reschedules",
            subject=task.owner,
            entity_type="work.Task",
            entity_id=task.id,
            summary=f"Deadline moved {task.reschedule_count} times: {task.title[:100]}",
            detail={
                "reschedule_count": task.reschedule_count,
                "original_due_at": task.original_due_at.isoformat(),
                "current_due_at": task.due_at.isoformat(),
                "reasons": list(
                    task.reschedules.order_by("-changed_at").values_list("reason", flat=True)[:5]
                ),
            },
        ):
            counts[ExceptionKind.REPEATED_RESCHEDULE] += 1

    # Leads nobody owns.
    for lead in (
        Lead.objects.filter(
            workspace_id=workspace_id,
            deleted_at__isnull=True,
            owner__isnull=True,
            created_at__lt=now - timedelta(hours=4),
        )
        .exclude(status=LeadStatus.DISQUALIFIED)
        .select_related("contact")[:200]
    ):
        if _raise_exception(
            kind=ExceptionKind.NEGLECTED_LEAD,
            dedupe_key=f"lead:{lead.id}:unowned",
            subject=None,
            entity_type="crm.Lead",
            entity_id=lead.id,
            summary=f"Lead has no owner: {lead.contact.display_name}",
            detail={"created_at": lead.created_at.isoformat(), "status": lead.status},
        ):
            counts[ExceptionKind.NEGLECTED_LEAD] += 1

    # Open deals with nothing scheduled next.
    for deal in Opportunity.objects.filter(
        workspace_id=workspace_id,
        deleted_at__isnull=True,
        stage__in=OPEN_STAGES,
    ).filter(Q(next_action_at__isnull=True) | Q(next_action_at__lt=now))[:200]:
        if _raise_exception(
            kind=ExceptionKind.NO_NEXT_ACTION,
            dedupe_key=f"opportunity:{deal.id}:no_next_action",
            subject=deal.owner,
            entity_type="crm.Opportunity",
            entity_id=deal.id,
            summary=f"Open deal with no next action: {deal.service[:120]}",
            detail={
                "stage": deal.stage,
                "last_activity_at": deal.last_activity_at.isoformat()
                if deal.last_activity_at
                else None,
            },
        ):
            counts[ExceptionKind.NO_NEXT_ACTION] += 1

    # Handovers nobody accepted or returned.
    for client in Client.objects.filter(
        workspace_id=workspace_id,
        deleted_at__isnull=True,
        status=ClientStatus.PENDING_HANDOVER,
        created_at__lt=now - timedelta(days=2),
    )[:200]:
        if _raise_exception(
            kind=ExceptionKind.HANDOVER_INCOMPLETE,
            dedupe_key=f"client:{client.id}:handover",
            subject=client.delivery_owner,
            entity_type="crm.Client",
            entity_id=client.id,
            summary=f"Handover still unaccepted: {client.display_name}",
            detail={
                "created_at": client.created_at.isoformat(),
                "returned_reason": client.handover_returned_reason,
                "has_delivery_owner": client.delivery_owner_id is not None,
            },
        ):
            counts[ExceptionKind.HANDOVER_INCOMPLETE] += 1

    # Blocked or overdue delivery.
    for milestone in (
        Milestone.objects.filter(workspace_id=workspace_id, deleted_at__isnull=True)
        .filter(
            Q(status=MilestoneStatus.BLOCKED)
            | Q(due_on__lt=now.date())
            & ~Q(status__in=[MilestoneStatus.ACCEPTED, MilestoneStatus.CANCELLED])
        )
        .select_related("project")[:200]
    ):
        if _raise_exception(
            kind=ExceptionKind.DELIVERY_BLOCKED,
            dedupe_key=f"milestone:{milestone.id}:blocked",
            subject=milestone.owner,
            entity_type="work.Milestone",
            entity_id=milestone.id,
            summary=f"Delivery at risk: {milestone.name[:120]}",
            detail={
                "project": milestone.project.name,
                "status": milestone.status,
                "due_on": milestone.due_on.isoformat() if milestone.due_on else None,
                "blocked_reason": milestone.blocked_reason,
            },
        ):
            counts[ExceptionKind.DELIVERY_BLOCKED] += 1

    # Tickets that came back after being resolved.
    for ticket in Ticket.objects.filter(
        workspace_id=workspace_id, deleted_at__isnull=True, reopen_count__gt=0
    )[:200]:
        if _raise_exception(
            kind=ExceptionKind.TICKET_REOPENED,
            dedupe_key=f"ticket:{ticket.id}:reopened:{ticket.reopen_count}",
            subject=ticket.owner,
            entity_type="support.Ticket",
            entity_id=ticket.id,
            summary=f"Ticket reopened: {ticket.title[:120]}",
            detail={"reopen_count": ticket.reopen_count, "state": ticket.state},
        ):
            counts[ExceptionKind.TICKET_REOPENED] += 1

    return {k: v for k, v in counts.items() if v}


def _raise_exception(
    *,
    kind: str,
    dedupe_key: str,
    subject,
    entity_type: str,
    entity_id: uuid.UUID,
    summary: str,
    detail: dict,
) -> bool:
    """Create one exception, or do nothing if it is already open."""
    try:
        with transaction.atomic():
            WorkException.objects.create(
                workspace_id=current_workspace_id(),
                kind=kind,
                state=ExceptionState.OPEN,
                dedupe_key=dedupe_key,
                subject=subject,
                entity_type=entity_type,
                entity_id=entity_id,
                summary=summary,
                detail=detail,
            )
        return True
    except IntegrityError:
        # Already open. The partial unique index is doing its job.
        return False


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


def visible_exceptions(membership: Membership) -> QuerySet[WorkException]:
    """Exceptions this person may see.

    An employee sees their own — they have a right to see what has been recorded
    about their work, and to respond to it (CRM10). A manager sees the queue.
    """
    queryset = WorkException.objects.filter(
        workspace_id=current_workspace_id(), deleted_at__isnull=True
    ).select_related("subject", "reviewed_by")

    if membership.role in {Role.OWNER, Role.SALES_MANAGER, Role.DELIVERY_MANAGER}:
        return queryset.order_by("-created_at")
    return queryset.filter(subject_id=membership.user_id).order_by("-created_at")


@transaction.atomic
def explain(
    *,
    membership: Membership,
    actor: User,
    exception_id: uuid.UUID,
    explanation: str,
    dispute: bool = False,
    request_id: str = "",
) -> WorkException:
    """The employee responds.

    They may explain, or explicitly dispute. A disputed exception stays visible
    and goes to a manager — it is not dropped, and it does not stand unanswered.
    """
    if not explanation.strip():
        raise ValidationFailed(
            "Write your explanation.",
            field_errors={"explanation": ["This field is required."]},
        )

    exception = WorkException.objects.select_for_update().get(
        pk=exception_id, workspace_id=current_workspace_id()
    )

    is_manager = membership.role in {
        Role.OWNER,
        Role.SALES_MANAGER,
        Role.DELIVERY_MANAGER,
    }
    if exception.subject_id != membership.user_id and not is_manager:
        raise NotAuthorized("This exception concerns another person's work.")

    exception.employee_explanation = explanation.strip()
    exception.employee_responded_at = timezone.now()
    exception.state = ExceptionState.DISPUTED if dispute else ExceptionState.UNDER_REVIEW
    exception.version += 1
    exception.save()

    record_audit(
        action=AuditAction.UPDATE,
        entity_type="reporting.WorkException",
        entity_id=exception.id,
        actor=actor,
        changes={"state": {"to": exception.state}, "disputed": dispute},
        reason=explanation.strip()[:500],
        request_id=request_id,
    )
    return exception


@transaction.atomic
def review(
    *,
    membership: Membership,
    actor: User,
    exception_id: uuid.UUID,
    decision: str,
    dismiss: bool = False,
    expected_version: int | None = None,
    request_id: str = "",
) -> WorkException:
    """A manager records a decision, with a reason.

    Closing an exception always requires a written decision — the constraint is
    in the database too. "Resolved" with no reason is indistinguishable from
    somebody clearing their queue.

    This records a judgement about a piece of work. It is not a disciplinary
    action, and the system takes none.
    """
    _require(membership, "exception.review")

    if not decision.strip():
        raise ValidationFailed(
            "Record your decision and why you reached it.",
            field_errors={"decision": ["This field is required."]},
        )

    exception = WorkException.objects.select_for_update().get(
        pk=exception_id, workspace_id=current_workspace_id()
    )
    if expected_version is not None and exception.version != expected_version:
        from modules.common.exceptions import VersionConflict

        raise VersionConflict(expected=expected_version, current=exception.version)

    exception.state = ExceptionState.DISMISSED if dismiss else ExceptionState.RESOLVED
    exception.review_decision = decision.strip()
    exception.reviewed_by = actor
    exception.reviewed_at = timezone.now()
    exception.version += 1
    exception.save()

    record_audit(
        action=AuditAction.UPDATE,
        entity_type="reporting.WorkException",
        entity_id=exception.id,
        actor=actor,
        changes={"state": {"to": exception.state}},
        reason=decision.strip()[:500],
        request_id=request_id,
    )
    return exception
