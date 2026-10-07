"""Durable job worker (SVX-TECH-001 section 8.2, RD04).

Claiming and executing are separate transactions on purpose:

1. A *short* transaction claims a due job with ``SELECT ... FOR UPDATE
   SKIP LOCKED``, stamps a random lease token and commits. Holding a row lock
   for the whole execution would serialise the queue and block other workers.
2. A second transaction, inside the job's tenant context, evaluates current
   state, performs the action and completes the job.

The completion update is conditional on the lease token still matching. A
worker that stalled past its lease expiry -- paused, swapped, GC'd -- finds its
update affects zero rows and stops, rather than overwriting the result of the
worker that took the job over.
"""

from __future__ import annotations

import logging
import random
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from modules.automation.engine import JobOutcome, execute_job
from modules.automation.models import Job, JobState, SchedulerHeartbeat
from modules.common.tenancy import workspace_context

logger = logging.getLogger("scalevexo.worker")


class LeaseLost(RuntimeError):
    """The lease expired and another worker took the job."""


def claim_next_job(*, now=None) -> Job | None:
    """Claim one due job, or return None.

    Runs without a tenant context: the queue is cross-workspace metadata, which
    is precisely the minimal access the scheduler identity is granted (section
    18.2). The job's own workspace is used for the work itself.
    """
    now = now or timezone.now()
    lease_token = uuid.uuid4()
    lease_expiry = now + timedelta(seconds=settings.JOB_LEASE_SECONDS)
    alias = settings.SCHEDULER_DB_ALIAS

    with transaction.atomic(using=alias):
        job = (
            Job.objects.using(alias)
            .select_for_update(skip_locked=True)
            .filter(due_at__lte=now)
            .filter(
                # Pending work, or work whose previous lease has expired. The
                # expiry arm is what lets a crashed worker's jobs be recovered
                # without an operator intervening.
                Q(state=JobState.PENDING) | Q(state=JobState.LEASED, lease_expires_at__lt=now)
            )
            .order_by("due_at")
            .first()
        )
        if job is None:
            return None

        job.state = JobState.LEASED
        job.lease_token = lease_token
        job.lease_expires_at = lease_expiry
        job.attempts += 1
        job.save(
            using=alias,
            update_fields=["state", "lease_token", "lease_expires_at", "attempts"],
        )
        return job


def run_job(job: Job) -> str:
    """Execute a claimed job inside its tenant context.

    Returns the terminal outcome. Never raises for an ordinary business
    failure: those are recorded on the job so an operator can see and retry
    them.
    """
    token = job.lease_token
    try:
        with workspace_context(job.workspace_id):
            # Re-read on the application connection. The instance handed over by
            # the scheduler was loaded by a role that cannot follow its
            # relations, so the rule and source event are not populated on it.
            scoped = (
                Job.objects.select_related("rule", "rule__workspace", "source_event")
                .filter(pk=job.pk, lease_token=token)
                .first()
            )
            if scoped is None:
                raise LeaseLost
            outcome, detail = execute_job(scoped)
            _finalise(scoped, token, outcome, detail)
            return outcome
    except LeaseLost:
        logger.warning(
            "Lease expired before completion; another worker owns this job.",
            extra={"job_id": str(job.id), "job_type": job.job_type},
        )
        return JobState.LEASED
    except Exception as exc:  # noqa: BLE001 - a failing job must not kill the worker
        logger.exception(
            "Job raised an unexpected error.",
            extra={"job_id": str(job.id), "job_type": job.job_type},
        )
        _handle_failure(job, token, str(exc)[:500])
        return JobOutcome.FAILED


def _finalise(job: Job, token, outcome: str, detail: str) -> None:
    """Complete the job, but only if we still hold the lease."""
    if outcome == JobOutcome.FAILED:
        _handle_failure(job, token, detail)
        return

    state = JobState.SUCCEEDED if outcome == JobOutcome.SUCCEEDED else JobState.CANCELLED
    updated = Job.objects.filter(pk=job.pk, lease_token=token).update(
        state=state,
        completed_at=timezone.now(),
        cancelled_reason=detail[:200] if state == JobState.CANCELLED else "",
        last_error="",
        lease_token=None,
        lease_expires_at=None,
    )
    if updated == 0:
        raise LeaseLost


def _handle_failure(job: Job, token, detail: str) -> None:
    """Retry a transient failure with backoff, then move it to the failed queue.

    Backoff is 1, 5 and 30 minutes with jitter (section 8.2). Jitter matters
    because a provider outage otherwise produces a synchronised retry burst the
    moment it recovers.

    Note what this does *not* do: it never blindly repeats an external send
    after an ambiguous timeout. Release 1 has only internal database actions,
    which are idempotent through the RuleExecution key. When the email
    connector lands in Release 2, ambiguous sends must be reconciled against
    provider state instead of retried here.
    """
    attempts = job.attempts
    if attempts >= settings.JOB_MAX_ATTEMPTS:
        updated = Job.objects.filter(pk=job.pk, lease_token=token).update(
            state=JobState.FAILED,
            last_error=detail[:500],
            completed_at=timezone.now(),
            lease_token=None,
            lease_expires_at=None,
        )
        if updated == 0:
            raise LeaseLost
        logger.error(
            "Job exhausted its retries and moved to the failed queue.",
            extra={"job_id": str(job.id), "attempts": attempts},
        )
        return

    delays = settings.JOB_RETRY_DELAYS_MINUTES
    base = delays[min(attempts - 1, len(delays) - 1)]
    jitter = random.uniform(0.8, 1.2)  # noqa: S311 - scheduling jitter, not crypto
    next_due = timezone.now() + timedelta(minutes=base * jitter)

    updated = Job.objects.filter(pk=job.pk, lease_token=token).update(
        state=JobState.PENDING,
        due_at=next_due,
        last_error=detail[:500],
        lease_token=None,
        lease_expires_at=None,
    )
    if updated == 0:
        raise LeaseLost


def process_available_jobs(*, max_jobs: int = 50) -> dict[str, int]:
    """Drain up to ``max_jobs`` due jobs. Returns a count per outcome."""
    counts = {"succeeded": 0, "cancelled": 0, "failed": 0, "leased": 0}
    for _ in range(max_jobs):
        job = claim_next_job()
        if job is None:
            break
        outcome = run_job(job)
        counts[outcome] = counts.get(outcome, 0) + 1
    record_heartbeat("worker", detail=counts)
    return counts


def record_heartbeat(component: str, *, detail: dict | None = None) -> None:
    """Prove the component is alive.

    Monitored because a silently dead worker is otherwise indistinguishable
    from a quiet queue: both produce no alerts (section 12.1).
    """
    SchedulerHeartbeat.objects.update_or_create(
        component=component,
        defaults={"last_beat_at": timezone.now(), "detail": detail or {}},
    )
