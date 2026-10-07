"""Deterministic rule engine (SVX-TECH-001 section 8).

Two halves:

* ``plan_jobs_for_event`` decides *what should be scheduled* when a business
  fact commits. It runs in the dispatcher, outside any tenant context.
* ``execute_job`` decides *whether the action is still warranted* and performs
  it. It runs in the worker, inside the job's tenant context.

The split matters. State can change between scheduling and execution -- the
task gets completed, the deal closes, the owner changes, the rule is paused.
The worker therefore re-reads current state and re-checks every condition
immediately before acting, rather than trusting the payload it was handed
(Journey B step 3).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from modules.automation.models import (
    Job,
    Notification,
    NotificationState,
    OutboxEvent,
    RuleDefinition,
    RuleExecution,
)
from modules.automation.schemas import Action, Condition, RuleConfig
from modules.common.tenancy import current_workspace_id, workspace_context
from modules.identity.models import Membership, MembershipStatus, Role
from modules.identity.services import build_calendar

logger = logging.getLogger("scalevexo.automation")

UNSET = object()


@dataclass(slots=True)
class JobPlan:
    """A job the dispatcher intends to create."""

    job_type: str
    dedupe_key: str
    due_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    rule_id: uuid.UUID | None = None
    rule_version: int | None = None


def plan_jobs_for_event(event: OutboxEvent) -> list[JobPlan]:
    """Decide which rule actions this event should schedule.

    Loop control (section 8.3): a generated event must not re-enter the same
    rule version within its own causal chain, and chains are capped at
    ``RULE_MAX_CHAIN_DEPTH``.
    """
    if event.chain_depth >= settings.RULE_MAX_CHAIN_DEPTH:
        logger.warning(
            "Event reached the maximum chain depth; not scheduling further work.",
            extra={"event_id": str(event.id), "chain_depth": event.chain_depth},
        )
        return []

    plans: list[JobPlan] = []
    with workspace_context(event.workspace_id):
        rules = RuleDefinition.objects.filter(
            workspace_id=event.workspace_id,
            trigger=event.event_type,
            enabled=True,
            paused_at__isnull=True,
            deleted_at__isnull=True,
        )
        calendar = None
        for rule in rules:
            # An event whose own chain already includes this rule version would
            # otherwise loop.
            if _already_in_chain(event, rule):
                continue
            try:
                config = _load_config(rule)
            except ValueError:
                logger.exception(
                    "Stored rule failed schema validation; skipping.",
                    extra={"rule_id": str(rule.id)},
                )
                continue

            if calendar is None:
                calendar = build_calendar(rule.workspace)

            due_at = timezone.now()
            if config.delay is not None:
                minutes = config.delay.total_working_minutes(calendar.minutes_per_working_day)
                due_at = calendar.add_working_minutes(due_at, minutes)

            for index, action in enumerate(config.actions):
                plans.append(
                    JobPlan(
                        job_type=f"rule:{action.type}",
                        dedupe_key=_dedupe_key(event, rule, index, action),
                        due_at=due_at,
                        payload={
                            "action_index": index,
                            "aggregate_type": event.aggregate_type,
                            "aggregate_id": str(event.aggregate_id),
                        },
                        rule_id=rule.id,
                        rule_version=rule.rule_version,
                    )
                )
    return plans


def _dedupe_key(event: OutboxEvent, rule: RuleDefinition, index: int, action: Action) -> str:
    """Unique per (event, rule version, action).

    Prefixed with the aggregate so that ``cancel_jobs_for`` can invalidate every
    queued action about one task with a prefix match.
    """
    return (
        f"{event.aggregate_type}:{event.aggregate_id}"
        f"|{event.id}|{rule.template}@{rule.rule_version}|{index}:{action.type}"
    )


def _already_in_chain(event: OutboxEvent, rule: RuleDefinition) -> bool:
    if event.causation_id is None:
        return False
    return RuleExecution.objects.filter(
        workspace_id=event.workspace_id,
        rule=rule,
        rule_version=rule.rule_version,
        event__correlation_id=event.correlation_id,
    ).exists()


def _load_config(rule: RuleDefinition) -> RuleConfig:
    return RuleConfig.model_validate(
        {
            "template": rule.template,
            "version": rule.rule_version,
            "trigger": rule.trigger,
            "conditions": rule.conditions,
            "delay": rule.delay or None,
            "actions": rule.actions,
            "limits": rule.limits or {},
        }
    )


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------


def resolve_field(path: str, context: dict[str, Any]) -> Any:
    """Read an allowlisted dotted field from the evaluation context.

    Returns UNSET when the entity is absent, which is distinct from a present
    entity holding None. "The opportunity is missing" and "the opportunity has
    no next action" must not evaluate the same way.
    """
    entity_name, _, attribute = path.partition(".")
    entity = context.get(entity_name)
    if entity is None:
        return UNSET
    return getattr(entity, attribute, UNSET)


def evaluate_condition(condition: Condition, context: dict[str, Any]) -> bool:
    actual = resolve_field(condition.field, context)

    if condition.op == "is_empty":
        expected_empty = True if condition.value is None else bool(condition.value)
        is_empty = actual is UNSET or actual in (None, "", [])
        return is_empty is expected_empty

    if actual is UNSET:
        # A condition on an absent entity cannot be satisfied. Treating it as
        # true would fire rules about records that no longer exist.
        return False

    if condition.op == "eq":
        return _comparable(actual) == _comparable(condition.value)
    if condition.op == "neq":
        return _comparable(actual) != _comparable(condition.value)
    if condition.op == "in":
        return _comparable(actual) in {_comparable(v) for v in condition.value}
    if actual is None:
        # Ordering comparisons against unknown are false, not an error.
        return False
    if condition.op == "gt":
        return _comparable(actual) > _comparable(condition.value)
    if condition.op == "lt":
        return _comparable(actual) < _comparable(condition.value)
    return False


def _comparable(value: Any) -> Any:
    """Normalise for comparison without changing meaning.

    UUIDs arrive as strings from stored JSON but as UUID objects from the ORM,
    so both sides are reduced to strings before comparison.
    """
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def evaluate_conditions(conditions: list[Condition], context: dict[str, Any]) -> bool:
    """All conditions must hold. There is no OR in the Release 1 schema."""
    return all(evaluate_condition(condition, context) for condition in conditions)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


class JobOutcome:
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    FAILED = "failed"


def execute_job(job: Job) -> tuple[str, str]:
    """Run one leased job. Returns (outcome, detail).

    Called by the worker inside the job's workspace context and inside a
    transaction. The action, its RuleExecution row and the job completion are
    written atomically, so an at-least-once queue yields exactly-once effects
    for internal database actions (section 8.2).
    """
    rule = job.rule
    if rule is None or not rule.is_runnable:
        return JobOutcome.CANCELLED, "The rule is paused, disabled or deleted."
    if job.rule_version != rule.rule_version:
        return JobOutcome.CANCELLED, "The rule changed version after this job was queued."

    try:
        config = _load_config(rule)
    except ValueError as exc:
        return JobOutcome.FAILED, f"Rule configuration is invalid: {exc}"

    context = _build_context(job)
    if context is None:
        return JobOutcome.CANCELLED, "The record this job refers to no longer exists."

    # Re-check the conditions against *current* state, not the state at
    # scheduling time (Journey B step 3).
    if not evaluate_conditions(config.conditions, context):
        return JobOutcome.CANCELLED, "Conditions no longer hold."

    index = job.payload.get("action_index", 0)
    if index >= len(config.actions):
        return JobOutcome.CANCELLED, "The action no longer exists in this rule version."
    action = config.actions[index]

    try:
        # A savepoint, not a bare call. The unique RuleExecution key is expected
        # to fire on a repeat, and in PostgreSQL a failed statement poisons the
        # whole transaction: without this, catching the IntegrityError would
        # leave the connection unusable and the worker could not even record
        # that the job finished.
        with transaction.atomic():
            result = _perform_action(job, rule, action, context)
    except IntegrityError:
        # The effect already happened, so this is success, not failure.
        return JobOutcome.SUCCEEDED, "Already executed; duplicate suppressed."

    return JobOutcome.SUCCEEDED, result


def _build_context(job: Job) -> dict[str, Any] | None:
    """Load the current entities this job concerns.

    Returns None when the record has gone, which cancels the job rather than
    failing it: a reminder about a deleted record is not an error, it is simply
    no longer wanted.
    """
    return context_for(job.payload.get("aggregate_type", ""), job.payload.get("aggregate_id"))


def context_for(aggregate_type: str, aggregate_id) -> dict[str, Any] | None:
    """The current state of one record and its related records, as rules see it.

    Shared by job execution and by simulation, so a simulation evaluates exactly
    what a live job would.
    """
    from modules.crm.models import Client, Lead, Opportunity
    from modules.support.models import Ticket
    from modules.work.models import Milestone, Task

    if not aggregate_id:
        return None

    workspace_id = current_workspace_id()
    loaders = {
        "lead": (Lead, "lead"),
        "opportunity": (Opportunity, "opportunity"),
        "task": (Task, "task"),
        "client": (Client, "client"),
        "milestone": (Milestone, "milestone"),
        "ticket": (Ticket, "ticket"),
    }
    entry = loaders.get(aggregate_type)
    if entry is None:
        return None

    model, key = entry
    instance = (
        model.objects.filter(pk=aggregate_id, workspace_id=workspace_id, deleted_at__isnull=True)
        .select_related()
        .first()
    )
    if instance is None:
        return None

    # Make related entities available to conditions where they exist, so a rule
    # on a task can also test the deal it belongs to.
    context: dict[str, Any] = {key: instance}
    if key == "task":
        context["opportunity"] = instance.opportunity
        context["lead"] = instance.lead
    elif key == "opportunity":
        context["lead"] = instance.lead
    elif key == "milestone":
        context["project"] = instance.project
    elif key == "ticket":
        context["client"] = instance.client
        context["project"] = instance.project
    return context


def _perform_action(job: Job, rule: RuleDefinition, action: Action, context: dict[str, Any]) -> str:
    """Apply one action and record that it happened, atomically."""
    if action.type == "create_task":
        detail = _action_create_task(job, rule, action, context)
    elif action.type == "assign_owner":
        detail = _action_assign_owner(job, rule, context)
    elif action.type in ("notify_owner", "notify_manager"):
        detail = _action_notify(job, rule, action, context)
    else:  # pragma: no cover - schema restricts the set
        raise ValueError(f"Unsupported action {action.type!r}")

    RuleExecution.objects.create(
        workspace_id=current_workspace_id(),
        rule=rule,
        rule_version=rule.rule_version,
        event=job.source_event,
        event_key=job.dedupe_key,
        action_type=action.type,
        result={"detail": detail},
    )
    return detail


def _action_create_task(
    job: Job, rule: RuleDefinition, action: Action, context: dict[str, Any]
) -> str:
    from modules.work.services import create_task_from_rule

    opportunity = context.get("opportunity")
    lead = context.get("lead")
    owner = _resolve_owner(opportunity or lead)
    if owner is None:
        return "No eligible owner; no task created."

    task = create_task_from_rule(
        rule=rule,
        title=action.title or f"Follow up: {rule.name}",
        owner=owner,
        opportunity=opportunity,
        lead=lead,
        contact=getattr(opportunity or lead, "contact", None),
        due_in=action.due_in,
    )
    return f"Created task {task.id}."


def _action_assign_owner(job: Job, rule: RuleDefinition, context: dict[str, Any]) -> str:
    """Assign an unowned lead to an eligible member.

    An inactive or absent employee must not receive a new automatic assignment
    (rule catalogue preamble), so eligibility is re-checked here rather than
    trusting a roster read at scheduling time.
    """
    lead = context.get("lead")
    if lead is None or lead.owner_id is not None:
        return "Lead already has an owner; nothing to assign."

    candidate = (
        Membership.objects.filter(
            workspace_id=current_workspace_id(),
            status=MembershipStatus.ACTIVE,
            is_available_for_assignment=True,
            role__in=[Role.SALES_REP, Role.SALES_MANAGER],
        )
        .select_related("user")
        .order_by("created_at")
        .first()
    )
    if candidate is None:
        return "No eligible assignee is available; leaving the lead unassigned."

    lead.owner = candidate.user
    lead.version += 1
    lead.save(update_fields=["owner", "version", "updated_at"])
    return f"Assigned lead to {candidate.user_id}."


def _action_notify(job: Job, rule: RuleDefinition, action: Action, context: dict[str, Any]) -> str:
    # Most specific entity first: an alert about a milestone should link to the
    # milestone, not to the project it happens to belong to.
    entity = next(
        (
            context[key]
            for key in ("task", "milestone", "ticket", "client", "opportunity", "lead")
            if context.get(key) is not None
        ),
        None,
    )
    if entity is None:
        return "Nothing to notify about."

    if action.type == "notify_owner":
        recipient_id = getattr(entity, "owner_id", None)
    else:
        recipient_id = _resolve_manager_id()

    if recipient_id is None:
        return "No eligible recipient; no alert raised."

    entity_type = type(entity).__name__.lower()
    dedupe_key = f"{entity_type}:{entity.pk}|{rule.template}@{rule.rule_version}"

    # One unresolved alert per cause (rule A01). A repeat while the first is
    # still open is suppressed by the partial unique index.
    _, created = Notification.objects.get_or_create(
        workspace_id=current_workspace_id(),
        dedupe_key=dedupe_key,
        resolved_at=None,
        defaults={
            "recipient_id": recipient_id,
            "channel": action.channel,
            "state": NotificationState.PENDING,
            "title": action.title or rule.name,
            "body": action.body or "",
            "cause": rule.explanation or f"Raised by rule {rule.name} v{rule.rule_version}.",
            "entity_type": entity_type,
            "entity_id": entity.pk,
            "rule": rule,
        },
    )
    return "Alert raised." if created else "An unresolved alert already exists."


def _resolve_owner(entity) -> Any:
    owner = getattr(entity, "owner", None)
    if owner is None:
        return None
    membership = Membership.objects.filter(workspace_id=current_workspace_id(), user=owner).first()
    if membership is None or not membership.can_receive_assignment:
        return None
    return owner


def _resolve_manager_id() -> uuid.UUID | None:
    membership = (
        Membership.objects.filter(
            workspace_id=current_workspace_id(),
            status=MembershipStatus.ACTIVE,
            role__in=[Role.SALES_MANAGER, Role.OWNER],
        )
        .order_by("role", "created_at")
        .first()
    )
    return membership.user_id if membership else None


@transaction.atomic
def simulate(rule: RuleDefinition, context: dict[str, Any]) -> dict[str, Any]:
    """Show which conditions matched and what would happen, without acting.

    Simulation never calls an external provider (section 6.1) and never writes.
    An administrator sees this before activating a rule.
    """
    config = _load_config(rule)
    matches = [
        {
            "field": condition.field,
            "op": condition.op,
            "value": condition.value,
            "actual": _comparable(resolve_field(condition.field, context)),
            "matched": evaluate_condition(condition, context),
        }
        for condition in config.conditions
    ]
    would_fire = all(entry["matched"] for entry in matches)
    return {
        "rule": f"{rule.template}@{rule.rule_version}",
        "conditions": matches,
        "would_fire": would_fire,
        "proposed_actions": [a.type for a in config.actions] if would_fire else [],
    }
