"""Outbox writing and dispatch (SVX-TECH-001 section 3.2, 8.2)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from modules.automation.models import Job, JobState, OutboxEvent, RuleDefinition
from modules.common.tenancy import current_workspace_id, workspace_context

logger = logging.getLogger("scalevexo.automation")

DISPATCH_BATCH_SIZE = 200


def emit(
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    aggregate_version: int = 1,
    payload: dict[str, Any] | None = None,
    correlation_id: uuid.UUID | None = None,
    causation_id: uuid.UUID | None = None,
    chain_depth: int = 0,
) -> OutboxEvent:
    """Record a business fact for later dispatch.

    Must be called inside the transaction that performs the change. Callers do
    not enqueue jobs directly: a job created before commit would be visible to
    a worker that then found no corresponding business record.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("emit() must run inside the transaction that performs the change.")

    return OutboxEvent.objects.create(
        workspace_id=current_workspace_id(),
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        payload=payload or {},
        correlation_id=correlation_id or uuid.uuid4(),
        causation_id=causation_id,
        chain_depth=chain_depth,
    )


def pending_event_refs(*, limit: int = DISPATCH_BATCH_SIZE) -> list[tuple]:
    """Discover undispatched events across all workspaces.

    This is the one question no workspace-scoped role can answer, so it runs on
    the scheduler connection, which is granted the queue tables and nothing
    else (section 18.2). Only identifiers come back -- no payloads, no CRM data.
    """
    alias = settings.SCHEDULER_DB_ALIAS
    return list(
        OutboxEvent.objects.using(alias)
        .filter(dispatched_at__isnull=True)
        .order_by("occurred_at")
        .values_list("id", "workspace_id")[:limit]
    )


def dispatch_pending(*, limit: int = DISPATCH_BATCH_SIZE) -> int:
    """Turn committed outbox events into jobs.

    Discovery is cross-workspace; everything that touches business data runs
    inside the event's own tenant context on the application connection. That
    split is what keeps the scheduler identity from ever needing to read a rule,
    a lead or a contact.

    Returns the number of events dispatched.
    """
    from modules.automation.engine import plan_jobs_for_event

    dispatched = 0
    for event_id, workspace_id in pending_event_refs(limit=limit):
        with workspace_context(workspace_id):
            # Re-read under lock inside the tenant context: two dispatcher runs
            # must not both act on one event.
            locked = (
                OutboxEvent.objects.select_for_update(skip_locked=True)
                .filter(pk=event_id, dispatched_at__isnull=True)
                .first()
            )
            if locked is None:
                continue

            for plan in plan_jobs_for_event(locked):
                _insert_job(locked, plan)

            locked.dispatched_at = timezone.now()
            locked.save(update_fields=["dispatched_at"])
            dispatched += 1

    return dispatched


def _insert_job(event: OutboxEvent, plan) -> Job | None:
    """Insert one job, tolerating an existing duplicate.

    The unique dedupe key is what makes repeated processing of one event
    produce one business action (CRM06 acceptance).
    """
    try:
        with transaction.atomic():
            return Job.objects.create(
                workspace_id=event.workspace_id,
                job_type=plan.job_type,
                dedupe_key=plan.dedupe_key,
                source_event=event,
                rule_id=plan.rule_id,
                rule_version=plan.rule_version,
                payload=plan.payload,
                due_at=plan.due_at,
                state=JobState.PENDING,
                correlation_id=event.correlation_id,
                chain_depth=event.chain_depth,
            )
    except IntegrityError:
        logger.info(
            "Job already exists for this event and rule version; skipping.",
            extra={"dedupe_key": plan.dedupe_key, "event_id": str(event.id)},
        )
        return None


def cancel_jobs_for(
    *, dedupe_prefix: str, reason: str, workspace_id: uuid.UUID | None = None
) -> int:
    """Cancel queued work that a business change has made obsolete.

    External replies, closure, reassignment and rescheduling all invalidate
    pending follow-up jobs (SVX-PRD-001 section 5.2). Cancelling is not
    optional politeness: without it, a reminder fires about a task the user
    already completed, and the system looks broken.
    """
    queryset = Job.objects.filter(
        state__in=[JobState.PENDING, JobState.LEASED],
        dedupe_key__startswith=dedupe_prefix,
    )
    if workspace_id is not None:
        queryset = queryset.filter(workspace_id=workspace_id)

    return queryset.update(
        state=JobState.CANCELLED,
        cancelled_reason=reason[:200],
        completed_at=timezone.now(),
        lease_token=None,
        lease_expires_at=None,
    )


def cancel_jobs_for_rule(rule: RuleDefinition, *, reason: str) -> int:
    """Pausing a rule must prevent its pending actions from executing (CRM06)."""
    return Job.objects.filter(rule=rule, state__in=[JobState.PENDING, JobState.LEASED]).update(
        state=JobState.CANCELLED,
        cancelled_reason=reason[:200],
        completed_at=timezone.now(),
        lease_token=None,
        lease_expires_at=None,
    )
