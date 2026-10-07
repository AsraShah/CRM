"""Task services (CRM05).

Completing a follow-up offers the next action or an explicit stop reason.
Rescheduling preserves the original deadline. Both exist so that the record of
what was promised survives the record of what was done.
"""

from __future__ import annotations

import uuid

from django.db import transaction
from django.utils import timezone

from modules.automation import outbox
from modules.automation.models import RuleDefinition
from modules.automation.schemas import Delay
from modules.common.audit import AuditAction, diff_fields, record_audit, snapshot
from modules.common.exceptions import NotAuthorized, ValidationFailed
from modules.common.tenancy import assert_same_workspace, current_workspace_id
from modules.identity.models import Membership, Role, User
from modules.identity.policy import has_permission
from modules.identity.services import assert_expected_version, build_calendar
from modules.work.models import Task, TaskKind, TaskOrigin, TaskReschedule, TaskStatus


def _require(membership: Membership, permission: str) -> None:
    if not has_permission(membership, permission):
        raise NotAuthorized(f"This role may not perform {permission}.")


def _assert_can_act_on(membership: Membership, task: Task) -> None:
    if membership.role in {Role.OWNER, Role.SALES_MANAGER, Role.DELIVERY_MANAGER}:
        return
    if task.owner_id and task.owner_id != membership.user_id:
        raise NotAuthorized("This task belongs to another user.")


@transaction.atomic
def create_task(
    *,
    membership: Membership,
    actor: User,
    title: str,
    due_at,
    owner: User | None = None,
    kind: str = TaskKind.FOLLOW_UP,
    description: str = "",
    contact=None,
    opportunity=None,
    lead=None,
    project=None,
    milestone=None,
    request_id: str = "",
) -> Task:
    """Create a task. An owner and a due time are both required (CRM05)."""
    _require(membership, "task.manage")

    resolved_owner = owner or actor
    if resolved_owner.id != membership.user_id:
        _require(membership, "task.assign_others")

    if not title.strip():
        raise ValidationFailed(
            "A task needs a title.", field_errors={"title": ["This field is required."]}
        )
    if due_at is None:
        raise ValidationFailed(
            "A task needs a due time.",
            field_errors={"due_at": ["This field is required."]},
        )

    if milestone is not None and project is None:
        project = milestone.project
    if milestone is not None and milestone.project_id != project.id:
        raise ValidationFailed(
            "That milestone belongs to a different project.",
            field_errors={"milestone": ["Not part of this project."]},
        )
    assert_same_workspace(
        *(o for o in (contact, opportunity, lead, project, milestone) if o is not None)
    )

    workspace = membership.workspace
    task = Task.objects.create(
        workspace_id=current_workspace_id(),
        title=title.strip(),
        description=description,
        kind=kind,
        owner=resolved_owner,
        created_by=actor,
        origin=TaskOrigin.USER,
        due_at=due_at,
        original_due_at=due_at,
        calendar_version=workspace.calendar_version,
        contact=contact,
        opportunity=opportunity,
        lead=lead,
        project=project,
        milestone=milestone,
    )

    if opportunity is not None:
        _refresh_next_action(opportunity)

    record_audit(
        action=AuditAction.CREATE,
        entity_type="work.Task",
        entity_id=task.id,
        actor=actor,
        changes={"title": task.title, "due_at": due_at, "owner_id": resolved_owner.id},
        request_id=request_id,
    )
    return task


def create_task_from_rule(
    *,
    rule: RuleDefinition,
    title: str,
    owner: User,
    opportunity=None,
    lead=None,
    contact=None,
    due_in: Delay | None = None,
) -> Task:
    """Create a task on behalf of an automation rule.

    Called from the worker, so it takes no membership: the rule's authority was
    established when an administrator enabled it. The origin is recorded as
    RULE so the exception view can tell a person's own commitment from one the
    system generated for them.
    """
    calendar = build_calendar(rule.workspace)
    due_at = timezone.now()
    if due_in is not None:
        due_at = calendar.add_working_minutes(
            due_at, due_in.total_working_minutes(calendar.minutes_per_working_day)
        )
    else:
        due_at = calendar.next_working_instant(due_at)

    task = Task.objects.create(
        workspace_id=current_workspace_id(),
        title=title[:300],
        kind=TaskKind.FOLLOW_UP,
        owner=owner,
        created_by=None,
        origin=TaskOrigin.RULE,
        due_at=due_at,
        original_due_at=due_at,
        calendar_version=rule.workspace.calendar_version,
        contact=contact,
        opportunity=opportunity,
        lead=lead,
        description=f"Created automatically by rule {rule.name} v{rule.rule_version}.",
    )
    if opportunity is not None:
        _refresh_next_action(opportunity)

    record_audit(
        action=AuditAction.RULE_ACTION,
        entity_type="work.Task",
        entity_id=task.id,
        actor=None,
        changes={"title": task.title, "due_at": due_at, "rule": rule.template},
        reason=f"Rule {rule.template} v{rule.rule_version}",
    )
    return task


@transaction.atomic
def complete_task(
    *,
    membership: Membership,
    actor: User,
    task_id: uuid.UUID,
    outcome: str,
    expected_version: int,
    next_action_title: str = "",
    next_action_due_at=None,
    stop_reason: str = "",
    request_id: str = "",
) -> tuple[Task, Task | None]:
    """Complete a task, capturing either a next action or a stop reason.

    Returning both records lets the interface offer the next step in the same
    flow, which is what keeps next-action coverage high without a separate
    screen (CRM05, SVX-PRD-001 section 7.1).
    """
    _require(membership, "task.manage")

    task = Task.objects.select_for_update().get(
        pk=task_id, workspace_id=current_workspace_id(), deleted_at__isnull=True
    )
    _assert_can_act_on(membership, task)
    assert_expected_version(task, expected_version)

    if task.status == TaskStatus.COMPLETED:
        # Completing twice is not an error; the first completion stands.
        return task, None
    if not outcome.strip() and not stop_reason.strip():
        raise ValidationFailed(
            "Completing a task requires an outcome, or a reason for stopping "
            "without a next action.",
            field_errors={"outcome": ["Provide an outcome or a stop reason."]},
        )
    if not next_action_title and not stop_reason.strip():
        raise ValidationFailed(
            "Record the next action, or an explicit reason for not scheduling one.",
            field_errors={"stop_reason": ["Required when there is no next action."]},
        )

    before = snapshot(task, ["status", "outcome", "completed_at"])

    task.status = TaskStatus.COMPLETED
    task.outcome = outcome.strip()
    task.stop_reason = stop_reason.strip()
    task.completed_at = timezone.now()
    task.completed_by = actor
    task.version += 1
    task.save()

    next_task = None
    if next_action_title:
        if next_action_due_at is None:
            raise ValidationFailed(
                "A next action needs a due time.",
                field_errors={"next_action_due_at": ["This field is required."]},
            )
        next_task = create_task(
            membership=membership,
            actor=actor,
            title=next_action_title,
            due_at=next_action_due_at,
            owner=task.owner,
            kind=task.kind,
            contact=task.contact,
            opportunity=task.opportunity,
            lead=task.lead,
            request_id=request_id,
        )

    if task.opportunity_id:
        _refresh_next_action(task.opportunity)

    record_audit(
        action=AuditAction.UPDATE,
        entity_type="work.Task",
        entity_id=task.id,
        actor=actor,
        changes=diff_fields(before, snapshot(task, ["status", "outcome", "completed_at"])),
        request_id=request_id,
    )
    outbox.emit(
        event_type="task.completed",
        aggregate_type="task",
        aggregate_id=task.id,
        aggregate_version=task.version,
        payload={"has_next_action": next_task is not None},
    )
    # A completed task must not still generate an overdue reminder.
    outbox.cancel_jobs_for(
        dedupe_prefix=f"task:{task.id}",
        reason="Task completed",
        workspace_id=task.workspace_id,
    )
    return task, next_task


@transaction.atomic
def reschedule_task(
    *,
    membership: Membership,
    actor: User,
    task_id: uuid.UUID,
    new_due_at,
    reason: str,
    expected_version: int,
    request_id: str = "",
) -> Task:
    """Move a deadline, preserving the one it replaces.

    ``original_due_at`` is never touched, and every change appends a
    TaskReschedule row. A changed deadline therefore cannot erase the original
    missed commitment (CRM05 acceptance).
    """
    _require(membership, "task.manage")

    if not reason.strip():
        raise ValidationFailed(
            "Rescheduling requires a reason.",
            field_errors={"reason": ["This field is required."]},
        )

    task = Task.objects.select_for_update().get(
        pk=task_id, workspace_id=current_workspace_id(), deleted_at__isnull=True
    )
    _assert_can_act_on(membership, task)
    assert_expected_version(task, expected_version)

    if task.status != TaskStatus.OPEN:
        raise ValidationFailed("Only an open task can be rescheduled.")

    previous_due_at = task.due_at
    TaskReschedule.objects.create(
        id=uuid.uuid4(),
        workspace_id=task.workspace_id,
        task=task,
        previous_due_at=previous_due_at,
        new_due_at=new_due_at,
        reason=reason.strip(),
        actor=actor,
        changed_at=timezone.now(),
    )

    task.due_at = new_due_at
    task.reschedule_count += 1
    task.calendar_version = membership.workspace.calendar_version
    task.version += 1
    task.save()

    if task.opportunity_id:
        _refresh_next_action(task.opportunity)

    record_audit(
        action=AuditAction.UPDATE,
        entity_type="work.Task",
        entity_id=task.id,
        actor=actor,
        changes={"due_at": {"from": previous_due_at, "to": new_due_at}},
        reason=reason,
        request_id=request_id,
    )
    outbox.emit(
        event_type="task.rescheduled",
        aggregate_type="task",
        aggregate_id=task.id,
        aggregate_version=task.version,
        payload={"reschedule_count": task.reschedule_count},
    )
    # The old reminder refers to a deadline that no longer exists.
    outbox.cancel_jobs_for(
        dedupe_prefix=f"task:{task.id}",
        reason="Task rescheduled",
        workspace_id=task.workspace_id,
    )
    return task


def _refresh_next_action(opportunity) -> None:
    """Keep the opportunity's cached next action in step with its tasks.

    The task remains the source of truth; this denormalised pair only exists so
    that the pipeline and the "no next action" rule can be answered without
    joining the whole task table.
    """
    next_task = (
        Task.objects.filter(
            workspace_id=opportunity.workspace_id,
            opportunity=opportunity,
            status=TaskStatus.OPEN,
            deleted_at__isnull=True,
        )
        .order_by("due_at")
        .first()
    )
    opportunity.next_action_task = next_task
    opportunity.next_action_at = next_task.due_at if next_task else None
    opportunity.save(update_fields=["next_action_task", "next_action_at", "updated_at"])
