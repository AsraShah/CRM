"""Project and milestone services (CRM08).

Two separations carry the weight here:

* **Submission is not acceptance.** An employee submits evidence; an authorised
  reviewer accepts. The same person can do both only if they hold the reviewer
  role, and even then the two acts are separately recorded.
* **Scope changes are not defects.** Both are recorded, in the same table, with
  different kinds — because the question "why did this run over?" cannot be
  answered if they are mixed.

Reopening accepted work preserves the earlier evidence and reason. Acceptance
history is not overwritten (Journey D).
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
from modules.common.tenancy import current_workspace_id
from modules.identity.models import Membership, Role, User
from modules.identity.policy import has_permission
from modules.identity.services import assert_expected_version
from modules.work.models import (
    Milestone,
    MilestoneDependency,
    MilestoneStatus,
    Project,
    ProjectStatus,
    ScopeChange,
)

# Permitted milestone movement. Note that ACCEPTED is not terminal: reopening
# is allowed, deliberately and with evidence preserved.
ALLOWED_MILESTONE_TRANSITIONS: dict[str, frozenset[str]] = {
    MilestoneStatus.PLANNED: frozenset(
        {
            MilestoneStatus.READY,
            # Started directly: the ready state is bookkeeping the person
            # doing the work does not need.
            MilestoneStatus.IN_PROGRESS,
            MilestoneStatus.CANCELLED,
        }
    ),
    MilestoneStatus.READY: frozenset(
        {
            MilestoneStatus.IN_PROGRESS,
            MilestoneStatus.BLOCKED,
            MilestoneStatus.CANCELLED,
        }
    ),
    MilestoneStatus.IN_PROGRESS: frozenset(
        {
            MilestoneStatus.IN_REVIEW,
            MilestoneStatus.BLOCKED,
            MilestoneStatus.CANCELLED,
        }
    ),
    MilestoneStatus.BLOCKED: frozenset({MilestoneStatus.IN_PROGRESS, MilestoneStatus.CANCELLED}),
    MilestoneStatus.IN_REVIEW: frozenset(
        {
            MilestoneStatus.ACCEPTED,
            # Sent back: not an acceptance, and not a cancellation.
            MilestoneStatus.IN_PROGRESS,
        }
    ),
    MilestoneStatus.ACCEPTED: frozenset({MilestoneStatus.IN_PROGRESS}),
    MilestoneStatus.CANCELLED: frozenset(),
}


def _require(membership: Membership, permission: str) -> None:
    if not has_permission(membership, permission):
        raise NotAuthorized(f"This role may not perform {permission}.")


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


@transaction.atomic
def add_dependency(
    *,
    membership: Membership,
    actor: User,
    milestone_id: uuid.UUID,
    depends_on_id: uuid.UUID,
    request_id: str = "",
) -> MilestoneDependency:
    """Declare that one milestone waits on another.

    Rejects cycles before inserting. A cycle would make every milestone in it
    permanently unacceptable, and the user would have no way to see why.
    """
    _require(membership, "project.manage")

    workspace_id = current_workspace_id()
    milestone = Milestone.objects.get(
        pk=milestone_id, workspace_id=workspace_id, deleted_at__isnull=True
    )
    depends_on = Milestone.objects.get(
        pk=depends_on_id, workspace_id=workspace_id, deleted_at__isnull=True
    )

    if milestone.pk == depends_on.pk:
        raise ValidationFailed("A milestone cannot depend on itself.")
    if milestone.project_id != depends_on.project_id:
        raise ValidationFailed(
            "Dependencies are within one project.",
            field_errors={"depends_on": ["Belongs to a different project."]},
        )
    if _would_create_cycle(milestone_id=milestone.pk, depends_on_id=depends_on.pk):
        raise ValidationFailed(
            "That dependency would create a cycle, leaving both milestones "
            "permanently unacceptable.",
            code="dependency_cycle",
        )

    dependency = MilestoneDependency.objects.create(
        workspace_id=workspace_id, milestone=milestone, depends_on=depends_on
    )
    record_audit(
        action=AuditAction.CREATE,
        entity_type="work.MilestoneDependency",
        entity_id=dependency.id,
        actor=actor,
        changes={"milestone": str(milestone.id), "depends_on": str(depends_on.id)},
        request_id=request_id,
    )
    return dependency


def _would_create_cycle(*, milestone_id: uuid.UUID, depends_on_id: uuid.UUID) -> bool:
    """Walk forward from the proposed predecessor looking for the successor.

    If `depends_on` already (transitively) depends on `milestone`, adding this
    edge closes a loop. Iterative rather than recursive, with a visited set, so
    an existing cycle in the data cannot hang the request.
    """
    workspace_id = current_workspace_id()
    seen: set[uuid.UUID] = set()
    frontier = [depends_on_id]

    while frontier:
        current = frontier.pop()
        if current == milestone_id:
            return True
        if current in seen:
            continue
        seen.add(current)
        frontier.extend(
            MilestoneDependency.objects.filter(
                workspace_id=workspace_id, milestone_id=current
            ).values_list("depends_on_id", flat=True)
        )
    return False


def unmet_dependencies(milestone: Milestone) -> list[Milestone]:
    """Predecessors that are not yet accepted."""
    return list(
        Milestone.objects.filter(
            workspace_id=milestone.workspace_id,
            dependents__milestone_id=milestone.pk,
            deleted_at__isnull=True,
        ).exclude(status=MilestoneStatus.ACCEPTED)
    )


# ---------------------------------------------------------------------------
# Milestone lifecycle
# ---------------------------------------------------------------------------


@transaction.atomic
def start_milestone(
    *,
    membership: Membership,
    actor: User,
    milestone_id: uuid.UUID,
    expected_version: int,
    request_id: str = "",
) -> Milestone:
    """Begin work on a milestone.

    Conversion creates onboarding milestones as PLANNED. Without this step they
    could never reach IN_REVIEW, because submission only accepts work that is
    already in progress -- the delivery team would have a project they could
    look at but not act on.

    Accepts PLANNED or READY and lands on IN_PROGRESS, collapsing a
    bookkeeping distinction that means nothing to the person doing the work.

    Also resumes BLOCKED work once the blocker is cleared. The transition table
    permits BLOCKED -> IN_PROGRESS, and this is the only service that performs
    it; without it a blocked milestone, and its project, could never move again.
    """
    _require(membership, "milestone.submit")

    milestone = Milestone.objects.select_for_update().get(
        pk=milestone_id, workspace_id=current_workspace_id(), deleted_at__isnull=True
    )
    assert_expected_version(milestone, expected_version)

    if milestone.status == MilestoneStatus.IN_PROGRESS:
        # Already started. Saying so is friendlier than a conflict.
        return milestone
    if milestone.status not in {
        MilestoneStatus.PLANNED,
        MilestoneStatus.READY,
        MilestoneStatus.BLOCKED,
    }:
        raise TransitionNotAllowed(
            "Only planned, ready or blocked work can be started.",
            extra={"status": milestone.status},
        )

    before = snapshot(milestone, ["status"])
    milestone.status = MilestoneStatus.IN_PROGRESS
    milestone.blocked_reason = ""
    milestone.blocked_next_owner = None
    milestone.version += 1
    milestone.save()

    record_audit(
        action=AuditAction.TRANSITION,
        entity_type="work.Milestone",
        entity_id=milestone.id,
        actor=actor,
        changes=diff_fields(before, snapshot(milestone, ["status"])),
        request_id=request_id,
    )
    _refresh_project_status(milestone.project)
    return milestone


@transaction.atomic
def submit_milestone(
    *,
    membership: Membership,
    actor: User,
    milestone_id: uuid.UUID,
    evidence: str,
    expected_version: int,
    request_id: str = "",
) -> Milestone:
    """An employee submits completed work for review.

    This is a claim, not a completion. It requires evidence, and it does not
    change anything a report would count as delivered.
    """
    _require(membership, "milestone.submit")

    milestone = Milestone.objects.select_for_update().get(
        pk=milestone_id, workspace_id=current_workspace_id(), deleted_at__isnull=True
    )
    assert_expected_version(milestone, expected_version)

    if not evidence.strip():
        raise ValidationFailed(
            "Submitting work for review requires evidence of what was done.",
            field_errors={"evidence": ["This field is required."]},
        )
    _assert_transition(milestone.status, MilestoneStatus.IN_REVIEW)

    before = snapshot(milestone, ["status", "evidence", "submitted_at"])

    milestone.status = MilestoneStatus.IN_REVIEW
    milestone.evidence = evidence.strip()
    milestone.submitted_at = timezone.now()
    milestone.submitted_by = actor
    milestone.version += 1
    milestone.save()

    record_audit(
        action=AuditAction.TRANSITION,
        entity_type="work.Milestone",
        entity_id=milestone.id,
        actor=actor,
        changes=diff_fields(before, snapshot(milestone, ["status", "evidence", "submitted_at"])),
        request_id=request_id,
    )
    outbox.emit(
        event_type="milestone.submitted",
        aggregate_type="milestone",
        aggregate_id=milestone.id,
        aggregate_version=milestone.version,
    )
    return milestone


@transaction.atomic
def accept_milestone(
    *,
    membership: Membership,
    actor: User,
    milestone_id: uuid.UUID,
    expected_version: int,
    note: str = "",
    override_dependencies: bool = False,
    override_reason: str = "",
    request_id: str = "",
) -> Milestone:
    """An authorised reviewer accepts submitted work.

    A dependent milestone cannot be accepted before its predecessor unless a
    manager records an override (CRM08 acceptance). The override is stored with
    its reason rather than silently permitted — the whole value of the rule is
    that breaking it leaves a trace.
    """
    _require(membership, "milestone.accept")

    milestone = Milestone.objects.select_for_update().get(
        pk=milestone_id, workspace_id=current_workspace_id(), deleted_at__isnull=True
    )
    assert_expected_version(milestone, expected_version)

    if milestone.status != MilestoneStatus.IN_REVIEW:
        raise TransitionNotAllowed(
            "Only work submitted for review can be accepted.",
            extra={"status": milestone.status},
        )
    if not milestone.evidence:
        raise ValidationFailed("This milestone carries no evidence to review.")

    # A reviewer accepting their own submission is permitted only where the role
    # genuinely holds review authority; it is recorded either way.
    outstanding = unmet_dependencies(milestone)
    if outstanding:
        if not override_dependencies:
            raise TransitionNotAllowed(
                "This milestone depends on work that has not been accepted yet.",
                code="unmet_dependencies",
                extra={
                    "unmet": [
                        {"id": str(m.id), "name": m.name, "status": m.status} for m in outstanding
                    ]
                },
            )
        if membership.role not in {Role.OWNER, Role.DELIVERY_MANAGER}:
            raise NotAuthorized("Only a manager may override a dependency.")
        if not override_reason.strip():
            raise ValidationFailed(
                "An override must record why the dependency was bypassed.",
                field_errors={"override_reason": ["This field is required."]},
            )

    before = snapshot(milestone, ["status", "accepted_at"])

    milestone.status = MilestoneStatus.ACCEPTED
    milestone.accepted_at = timezone.now()
    milestone.accepted_by = actor
    milestone.acceptance_note = note
    if outstanding and override_dependencies:
        milestone.dependency_override_by = actor
        milestone.dependency_override_reason = override_reason.strip()
    milestone.version += 1
    milestone.save()

    record_audit(
        action=AuditAction.TRANSITION,
        entity_type="work.Milestone",
        entity_id=milestone.id,
        actor=actor,
        changes=diff_fields(before, snapshot(milestone, ["status", "accepted_at"])),
        reason=override_reason or note,
        request_id=request_id,
    )
    outbox.emit(
        event_type="milestone.accepted",
        aggregate_type="milestone",
        aggregate_id=milestone.id,
        aggregate_version=milestone.version,
        payload={"dependency_overridden": bool(outstanding and override_dependencies)},
    )
    _refresh_project_status(milestone.project)
    return milestone


@transaction.atomic
def reopen_milestone(
    *,
    membership: Membership,
    actor: User,
    milestone_id: uuid.UUID,
    reason: str,
    expected_version: int,
    request_id: str = "",
) -> Milestone:
    """Reopen accepted work, preserving the earlier evidence and reason.

    The prior acceptance is appended to the evidence rather than cleared, so
    "this was accepted once and then reopened" stays readable (CRM08).
    """
    _require(membership, "milestone.accept")

    if not reason.strip():
        raise ValidationFailed(
            "Reopening accepted work requires a reason.",
            field_errors={"reason": ["This field is required."]},
        )

    milestone = Milestone.objects.select_for_update().get(
        pk=milestone_id, workspace_id=current_workspace_id(), deleted_at__isnull=True
    )
    assert_expected_version(milestone, expected_version)

    if milestone.status != MilestoneStatus.ACCEPTED:
        raise TransitionNotAllowed("Only accepted work can be reopened.")

    previous_acceptance = (
        f"\n\n--- Accepted {milestone.accepted_at:%Y-%m-%d %H:%M} by "
        f"{milestone.accepted_by_id}, reopened: {reason.strip()} ---\n"
        f"{milestone.evidence}"
    )

    milestone.status = MilestoneStatus.IN_PROGRESS
    milestone.evidence = previous_acceptance
    milestone.accepted_at = None
    milestone.accepted_by = None
    milestone.acceptance_note = ""
    milestone.submitted_at = None
    milestone.version += 1
    milestone.save()

    record_audit(
        action=AuditAction.TRANSITION,
        entity_type="work.Milestone",
        entity_id=milestone.id,
        actor=actor,
        changes={"status": {"from": MilestoneStatus.ACCEPTED, "to": milestone.status}},
        reason=reason.strip(),
        request_id=request_id,
    )
    _refresh_project_status(milestone.project)
    return milestone


@transaction.atomic
def block_milestone(
    *,
    membership: Membership,
    actor: User,
    milestone_id: uuid.UUID,
    blocked_reason: str,
    next_owner_id: uuid.UUID,
    expected_version: int,
    request_id: str = "",
) -> Milestone:
    """Mark work blocked. Requires a description and a next owner (CRM08).

    Blocked work with no named next owner is how a project stalls invisibly.
    """
    _require(membership, "project.manage")

    if not blocked_reason.strip():
        raise ValidationFailed(
            "Say what is blocking this work.",
            field_errors={"blocked_reason": ["This field is required."]},
        )

    workspace_id = current_workspace_id()
    milestone = Milestone.objects.select_for_update().get(
        pk=milestone_id, workspace_id=workspace_id, deleted_at__isnull=True
    )
    assert_expected_version(milestone, expected_version)
    _assert_transition(milestone.status, MilestoneStatus.BLOCKED)

    next_owner = (
        Membership.objects.select_related("user")
        .filter(workspace_id=workspace_id, user_id=next_owner_id)
        .first()
    )
    if next_owner is None or not next_owner.can_receive_assignment:
        raise ValidationFailed(
            "Name an active member who can unblock this.",
            field_errors={"next_owner_id": ["Not available for assignment."]},
        )

    milestone.status = MilestoneStatus.BLOCKED
    milestone.blocked_reason = blocked_reason.strip()
    milestone.blocked_next_owner = next_owner.user
    milestone.version += 1
    milestone.save()

    record_audit(
        action=AuditAction.TRANSITION,
        entity_type="work.Milestone",
        entity_id=milestone.id,
        actor=actor,
        changes={"status": {"to": MilestoneStatus.BLOCKED}},
        reason=blocked_reason.strip(),
        request_id=request_id,
    )
    outbox.emit(
        event_type="milestone.blocked",
        aggregate_type="milestone",
        aggregate_id=milestone.id,
        aggregate_version=milestone.version,
    )
    _refresh_project_status(milestone.project)
    return milestone


def _assert_transition(current: str, target: str) -> None:
    allowed = ALLOWED_MILESTONE_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise TransitionNotAllowed(
            f"A milestone cannot move from {current} to {target}.",
            extra={"from_status": current, "allowed": sorted(allowed)},
        )


def _refresh_project_status(project: Project) -> None:
    """Derive the project's status from its milestones.

    Derived rather than manually set, so a project cannot be reported complete
    while work remains open underneath it.
    """
    statuses = set(
        Milestone.objects.filter(
            workspace_id=project.workspace_id, project=project, deleted_at__isnull=True
        )
        .exclude(status=MilestoneStatus.CANCELLED)
        .values_list("status", flat=True)
    )

    if not statuses:
        return
    if statuses == {MilestoneStatus.ACCEPTED}:
        new_status = ProjectStatus.COMPLETED
        completed_at = timezone.now()
    elif MilestoneStatus.BLOCKED in statuses:
        new_status = ProjectStatus.BLOCKED
        completed_at = None
    else:
        new_status = ProjectStatus.IN_PROGRESS
        completed_at = None

    if project.status != new_status:
        project.status = new_status
        project.completed_at = completed_at
        project.version += 1
        project.save(update_fields=["status", "completed_at", "version", "updated_at"])


# ---------------------------------------------------------------------------
# Scope changes
# ---------------------------------------------------------------------------


@transaction.atomic
def record_scope_change(
    *,
    membership: Membership,
    actor: User,
    project_id: uuid.UUID,
    kind: str,
    description: str,
    commercial_impact: str = "",
    milestone_id: uuid.UUID | None = None,
    request_id: str = "",
) -> ScopeChange:
    """Record a scope change or a defect, distinctly (CRM08).

    A defect is work we already owed. A scope change is work we agreed to add.
    Recording them under one label destroys the only honest answer to "why did
    this project take longer than we said?".
    """
    _require(membership, "project.manage")

    if kind not in {"scope_change", "defect"}:
        raise ValidationFailed("Kind must be scope_change or defect.")
    if not description.strip():
        raise ValidationFailed(
            "Describe the change.",
            field_errors={"description": ["This field is required."]},
        )

    workspace_id = current_workspace_id()
    project = Project.objects.get(pk=project_id, workspace_id=workspace_id, deleted_at__isnull=True)
    milestone = None
    if milestone_id:
        milestone = Milestone.objects.filter(
            pk=milestone_id, workspace_id=workspace_id, project=project
        ).first()

    change = ScopeChange.objects.create(
        workspace_id=workspace_id,
        project=project,
        milestone=milestone,
        kind=kind,
        description=description.strip(),
        commercial_impact=commercial_impact.strip(),
        requested_by=actor,
    )
    record_audit(
        action=AuditAction.CREATE,
        entity_type="work.ScopeChange",
        entity_id=change.id,
        actor=actor,
        changes={"kind": kind, "project": str(project.id)},
        request_id=request_id,
    )
    return change


@transaction.atomic
def cancel_milestone(
    *,
    membership: Membership,
    actor: User,
    milestone_id: uuid.UUID,
    reason: str,
    impact: str,
    expected_version: int,
    request_id: str = "",
) -> Milestone:
    """Cancel a milestone, recording why and what it affects (SVX-PRD-001 s.5).

    Cancellation is a delivery decision, not a quiet deletion: the record stays,
    with its reason and impact, and accepted work cannot be cancelled at all.
    """
    _require(membership, "project.manage")
    errors = {}
    if not reason.strip():
        errors["reason"] = ["Say why it is cancelled."]
    if not impact.strip():
        errors["impact"] = ["Say what this changes for the client or the plan."]
    if errors:
        raise ValidationFailed(
            "Cancelling a milestone needs a reason and its impact.", field_errors=errors
        )

    milestone = Milestone.objects.select_for_update().get(
        pk=milestone_id, workspace_id=current_workspace_id(), deleted_at__isnull=True
    )
    assert_expected_version(milestone, expected_version)
    _assert_transition(milestone.status, MilestoneStatus.CANCELLED)

    previous = milestone.status
    milestone.status = MilestoneStatus.CANCELLED
    milestone.cancelled_reason = reason.strip()
    milestone.cancellation_impact = impact.strip()
    milestone.version += 1
    milestone.save()

    record_audit(
        action=AuditAction.TRANSITION,
        entity_type="work.Milestone",
        entity_id=milestone.id,
        actor=actor,
        changes={"status": {"from": previous, "to": milestone.status}, "impact": impact.strip()},
        reason=reason.strip(),
        request_id=request_id,
    )
    _refresh_project_status(milestone.project)
    return milestone


@transaction.atomic
def return_milestone(
    *,
    membership: Membership,
    actor: User,
    milestone_id: uuid.UUID,
    reason: str,
    expected_version: int,
    request_id: str = "",
) -> Milestone:
    """A reviewer sends submitted work back instead of accepting it (CRM08).

    IN_REVIEW -> IN_PROGRESS was always permitted; this is the act that
    performs it. The submitted evidence is kept, prefixed with what the
    reviewer said, so the next submission is reviewed against it.
    """
    _require(membership, "milestone.accept")
    if not reason.strip():
        raise ValidationFailed(
            "Say what needs to change before it can be accepted.",
            field_errors={"reason": ["This field is required."]},
        )

    milestone = Milestone.objects.select_for_update().get(
        pk=milestone_id, workspace_id=current_workspace_id(), deleted_at__isnull=True
    )
    assert_expected_version(milestone, expected_version)
    if milestone.status != MilestoneStatus.IN_REVIEW:
        raise TransitionNotAllowed(
            "Only submitted work can be sent back.", extra={"status": milestone.status}
        )

    milestone.evidence = (
        f"--- Sent back {timezone.now():%Y-%m-%d %H:%M} by {actor.id}: {reason.strip()} ---\n"
        f"{milestone.evidence}"
    )
    milestone.status = MilestoneStatus.IN_PROGRESS
    milestone.submitted_at = None
    milestone.version += 1
    milestone.save()

    record_audit(
        action=AuditAction.TRANSITION,
        entity_type="work.Milestone",
        entity_id=milestone.id,
        actor=actor,
        changes={"status": {"from": MilestoneStatus.IN_REVIEW, "to": milestone.status}},
        reason=reason.strip(),
        request_id=request_id,
    )
    _refresh_project_status(milestone.project)
    return milestone
