"""TEST07 / RD04 - rules, durable jobs and loop prevention (CRM06).

The requirements under test: repeated processing of one event produces one
business action; pausing a rule prevents pending actions from executing; a
stale lease cannot overwrite a newer worker's result.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from modules.automation import outbox
from modules.automation.engine import evaluate_conditions
from modules.automation.models import (
    Job,
    JobState,
    Notification,
    RuleDefinition,
    RuleTemplate,
)
from modules.automation.schemas import Condition, RuleConfig
from modules.automation.worker import claim_next_job, process_available_jobs, run_job
from modules.common.tenancy import workspace_context
from modules.crm import services as crm_services

pytestmark = pytest.mark.django_db(
    transaction=True,
    # The dispatcher and worker read the queue through the scheduler role,
    # which is a separate connection alias (section 18.2).
    databases=["default", "owner", "scheduler"],
)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_rule_schema_rejects_unknown_fields():
    """A rule is configuration, not code: the surface is closed."""
    with pytest.raises(ValueError, match="Unknown condition field"):
        Condition(field="user.password", op="eq", value="x")


def test_rule_schema_rejects_the_email_channel_until_release_2():
    """External sending stays disabled until a connector is authorised (CRM06)."""
    with pytest.raises(ValueError, match="not available"):
        RuleConfig.model_validate(
            {
                "template": "overdue_followup",
                "trigger": "task.due",
                "actions": [{"type": "notify_owner", "channel": "email"}],
            }
        )


def test_rule_schema_caps_actions_and_chain_depth():
    with pytest.raises(ValueError):
        RuleConfig.model_validate(
            {
                "template": "overdue_followup",
                "trigger": "task.due",
                "actions": [{"type": "notify_owner"}] * 4,
            }
        )


def test_condition_on_absent_entity_does_not_fire():
    """ "The opportunity is missing" must not satisfy a condition about it."""
    conditions = [Condition(field="opportunity.stage", op="eq", value="proposal")]
    assert evaluate_conditions(conditions, {}) is False


def test_is_empty_distinguishes_unknown_from_present():
    conditions = [Condition(field="lead.owner_id", op="is_empty")]

    class Lead:
        owner_id = None

    assert evaluate_conditions(conditions, {"lead": Lead()}) is True

    class Owned:
        owner_id = uuid.uuid4()

    assert evaluate_conditions(conditions, {"lead": Owned()}) is False


# ---------------------------------------------------------------------------
# Outbox and dispatch
# ---------------------------------------------------------------------------


@pytest.fixture
def unassigned_lead_rule(workspace):
    with workspace_context(workspace.id):
        return RuleDefinition.objects.create(
            workspace=workspace,
            template=RuleTemplate.UNASSIGNED_LEAD,
            name="A01 Unassigned lead",
            rule_version=1,
            enabled=True,
            trigger="lead.created",
            conditions=[{"field": "lead.owner_id", "op": "is_empty", "value": None}],
            actions=[
                {
                    "type": "notify_manager",
                    "channel": "in_app",
                    "title": "Lead is still unassigned",
                }
            ],
            delay={"working_minutes": 30},
            limits={"max_actions": 1, "cooldown_minutes": 240, "max_chain_depth": 3},
            explanation="Nobody is responsible for contacting this person yet.",
        )


def test_committed_event_becomes_exactly_one_job(
    workspace, sales_manager, contact_factory, unassigned_lead_rule
):
    with workspace_context(workspace.id):
        contact = contact_factory(workspace)
        crm_services.create_lead(
            membership=sales_manager,
            actor=sales_manager.user,
            contact=contact,
            owner=None,
        )

    assert outbox.dispatch_pending() == 1
    # Counting jobs is a tenant-scoped read like any other, so it needs a
    # context. Without one it returns zero, which is the policy doing its job.
    with workspace_context(workspace.id):
        assert Job.objects.count() == 1

    # CRM06 acceptance: repeated processing of one event produces one action.
    assert outbox.dispatch_pending() == 0
    with workspace_context(workspace.id):
        assert Job.objects.count() == 1


def test_paused_rule_prevents_pending_actions(
    workspace, sales_manager, contact_factory, unassigned_lead_rule
):
    """CRM06 acceptance: pausing a rule prevents pending actions executing."""
    with workspace_context(workspace.id):
        contact = contact_factory(workspace)
        crm_services.create_lead(
            membership=sales_manager,
            actor=sales_manager.user,
            contact=contact,
            owner=None,
        )
    outbox.dispatch_pending()

    with workspace_context(workspace.id):
        unassigned_lead_rule.paused_at = timezone.now()
        unassigned_lead_rule.save(update_fields=["paused_at"])
        # Make the job due, then run it.
        Job.objects.update(due_at=timezone.now() - timedelta(minutes=1))

    process_available_jobs()

    with workspace_context(workspace.id):
        job = Job.objects.get()
        assert job.state == JobState.CANCELLED
        assert Notification.objects.count() == 0


def test_alert_carries_its_cause_and_is_deduplicated(
    workspace, sales_manager, contact_factory, unassigned_lead_rule
):
    with workspace_context(workspace.id):
        contact = contact_factory(workspace)
        crm_services.create_lead(
            membership=sales_manager,
            actor=sales_manager.user,
            contact=contact,
            owner=None,
        )
    outbox.dispatch_pending()
    with workspace_context(workspace.id):
        Job.objects.update(due_at=timezone.now() - timedelta(minutes=1))
    process_available_jobs()

    with workspace_context(workspace.id):
        alerts = list(Notification.objects.all())
        assert len(alerts) == 1
        # Every alert explains why it appeared (SVX-PRD-001 section 7.2).
        assert "responsible" in alerts[0].cause


def test_completed_task_cancels_its_queued_reminder(workspace, sales_rep, contact_factory):
    """A reminder about work already done makes the system look broken."""
    from modules.work import services as work_services

    with workspace_context(workspace.id):
        contact = contact_factory(workspace)
        task = work_services.create_task(
            membership=sales_rep,
            actor=sales_rep.user,
            title="Call the prospect",
            due_at=timezone.now() + timedelta(hours=1),
            contact=contact,
        )
        Job.objects.create(
            workspace=workspace,
            job_type="rule:notify_owner",
            dedupe_key=f"task:{task.id}|evt|overdue_followup@1|0:notify_owner",
            due_at=timezone.now() + timedelta(hours=3),
            state=JobState.PENDING,
        )

        work_services.complete_task(
            membership=sales_rep,
            actor=sales_rep.user,
            task_id=task.id,
            outcome="Spoke to them; sending a proposal.",
            expected_version=task.version,
            next_action_title="Send proposal",
            next_action_due_at=timezone.now() + timedelta(days=1),
        )

        assert Job.objects.get().state == JobState.CANCELLED


# ---------------------------------------------------------------------------
# Leases
# ---------------------------------------------------------------------------


def _make_job(workspace, dedupe_key: str, **overrides) -> Job:
    """Insert a job row directly.

    ``automation_job`` is a tenant table, so the insert needs a workspace
    context exactly like any other write. Claiming afterwards does not: the
    scheduler role reads the queue across workspaces by design.
    """
    with workspace_context(workspace.id):
        return Job.objects.create(
            workspace=workspace,
            job_type="rule:notify_owner",
            dedupe_key=dedupe_key,
            due_at=overrides.pop("due_at", timezone.now() - timedelta(minutes=5)),
            **overrides,
        )


def test_expired_lease_is_reclaimed(workspace):
    """A crashed worker's job must be recoverable without intervention."""
    _make_job(
        workspace,
        "task:x|evt|t@1|0:notify_owner",
        state=JobState.LEASED,
        lease_token=uuid.uuid4(),
        lease_expires_at=timezone.now() - timedelta(minutes=1),
    )
    claimed = claim_next_job()
    assert claimed is not None
    assert claimed.state == JobState.LEASED
    assert claimed.attempts == 1


def test_a_live_lease_is_not_stolen(workspace):
    _make_job(
        workspace,
        "task:y|evt|t@1|0:notify_owner",
        state=JobState.LEASED,
        lease_token=uuid.uuid4(),
        lease_expires_at=timezone.now() + timedelta(minutes=5),
    )
    assert claim_next_job() is None


def test_a_stale_worker_cannot_overwrite_a_newer_result(workspace):
    """The completion update is conditional on still holding the lease.

    This is the RD04 case: a worker that paused past its lease expiry must not
    stamp its outcome over the worker that took the job over.
    """
    job = _make_job(workspace, "task:z|evt|t@1|0:notify_owner", state=JobState.PENDING)
    stale = claim_next_job()
    assert stale is not None

    # Another worker takes over after the lease expires.
    with workspace_context(workspace.id):
        Job.objects.filter(pk=job.pk).update(
            lease_token=uuid.uuid4(),
            lease_expires_at=timezone.now() + timedelta(minutes=5),
            state=JobState.SUCCEEDED,
        )

    outcome = run_job(stale)
    with workspace_context(workspace.id):
        refreshed = Job.objects.get(pk=job.pk)
    # The newer worker's result stands.
    assert refreshed.state == JobState.SUCCEEDED
    assert outcome == JobState.LEASED


def test_job_dedupe_key_is_unique_per_workspace(workspace):
    from django.db import IntegrityError

    key = "task:dup|evt|t@1|0:notify_owner"
    _make_job(workspace, key, due_at=timezone.now())
    with pytest.raises(IntegrityError):
        _make_job(workspace, key, due_at=timezone.now())
