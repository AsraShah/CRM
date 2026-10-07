"""Section 8.2 — what the worker checks immediately before it acts.

A job is scheduled against the state of the world at one moment and executed at
another. Everything in between can change: the task gets completed, the owner
is suspended, the rule is paused or edited, the record is deleted.

The engine therefore re-reads current state and re-evaluates every condition
before performing an action (Journey B step 3). These tests drive each of those
branches directly, because a journey test only ever exercises the path where
nothing changed — which is the one case where the re-check does not matter.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from modules.automation.engine import JobOutcome, execute_job, plan_jobs_for_event
from modules.automation.models import (
    Job,
    Notification,
    OutboxEvent,
    RuleDefinition,
    RuleExecution,
    RuleTemplate,
)
from modules.common.tenancy import workspace_context
from modules.crm import services as crm_services
from modules.work.models import Task, TaskStatus

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def unowned_lead(workspace, sales_manager, contact_factory):
    with workspace_context(workspace.id):
        contact = contact_factory(workspace, display_name="Unclaimed Ltd")
        yield crm_services.create_lead(
            membership=sales_manager,
            actor=sales_manager.user,
            contact=contact,
            owner=None,
        )


@pytest.fixture
def owned_lead(workspace, sales_manager, sales_rep, contact_factory):
    with workspace_context(workspace.id):
        contact = contact_factory(workspace, display_name="Claimed Ltd")
        yield crm_services.create_lead(
            membership=sales_manager,
            actor=sales_manager.user,
            contact=contact,
            owner=sales_rep.user,
        )


def _rule(workspace, *, actions, conditions=None, enabled=True, version=1):
    return RuleDefinition.objects.create(
        workspace=workspace,
        template=RuleTemplate.UNASSIGNED_LEAD,
        name="Test rule",
        rule_version=version,
        enabled=enabled,
        trigger="lead.created",
        conditions=conditions or [],
        actions=actions,
        limits={"max_actions": 3, "cooldown_minutes": 0, "max_chain_depth": 3},
        explanation="Raised for testing.",
    )


def _job_for(workspace, rule, lead, *, action_index=0, rule_version=None) -> Job:
    event = OutboxEvent.objects.create(
        workspace=workspace,
        event_type="lead.created",
        aggregate_type="lead",
        aggregate_id=lead.id,
    )
    return Job.objects.create(
        workspace=workspace,
        job_type="rule:test",
        dedupe_key=f"lead:{lead.id}|{event.id}|t@1|{action_index}",
        source_event=event,
        rule=rule,
        rule_version=rule_version if rule_version is not None else rule.rule_version,
        payload={
            "action_index": action_index,
            "aggregate_type": "lead",
            "aggregate_id": str(lead.id),
        },
        due_at=timezone.now(),
    )


# ---------------------------------------------------------------------------
# Guards that cancel rather than act
# ---------------------------------------------------------------------------


def test_a_paused_rule_cancels_its_queued_job(workspace, unowned_lead):
    """CRM06: pausing a rule prevents pending actions from executing."""
    with workspace_context(workspace.id):
        rule = _rule(workspace, actions=[{"type": "notify_manager"}])
        job = _job_for(workspace, rule, unowned_lead)

        rule.paused_at = timezone.now()
        rule.save(update_fields=["paused_at"])

        outcome, detail = execute_job(job)

    assert outcome == JobOutcome.CANCELLED
    assert "paused" in detail


def test_a_disabled_rule_cancels_its_queued_job(workspace, unowned_lead):
    with workspace_context(workspace.id):
        rule = _rule(workspace, actions=[{"type": "notify_manager"}])
        job = _job_for(workspace, rule, unowned_lead)

        rule.enabled = False
        rule.save(update_fields=["enabled"])

        outcome, _ = execute_job(job)
    assert outcome == JobOutcome.CANCELLED


def test_an_edited_rule_cancels_work_queued_under_the_old_version(workspace, unowned_lead):
    """An edit must not silently change the meaning of work already in flight."""
    with workspace_context(workspace.id):
        rule = _rule(workspace, actions=[{"type": "notify_manager"}])
        job = _job_for(workspace, rule, unowned_lead, rule_version=1)

        rule.rule_version = 2
        rule.save(update_fields=["rule_version"])

        outcome, detail = execute_job(job)

    assert outcome == JobOutcome.CANCELLED
    assert "changed version" in detail


def test_a_deleted_record_cancels_rather_than_fails(workspace, unowned_lead):
    """A reminder about a deleted record is unwanted, not an error."""
    with workspace_context(workspace.id):
        rule = _rule(workspace, actions=[{"type": "notify_manager"}])
        job = _job_for(workspace, rule, unowned_lead)

        unowned_lead.deleted_at = timezone.now()
        unowned_lead.deleted_reason = "Merged into another record"
        unowned_lead.save(update_fields=["deleted_at", "deleted_reason"])

        outcome, detail = execute_job(job)

    assert outcome == JobOutcome.CANCELLED
    assert "no longer exists" in detail


def test_conditions_are_re_evaluated_against_current_state(workspace, sales_rep, unowned_lead):
    """The core Journey B step 3 guarantee.

    The job was queued because the lead had no owner. By the time it runs
    somebody has claimed it, so the alert must not fire.
    """
    with workspace_context(workspace.id):
        rule = _rule(
            workspace,
            actions=[{"type": "notify_manager", "title": "Unassigned"}],
            conditions=[{"field": "lead.owner_id", "op": "is_empty", "value": None}],
        )
        job = _job_for(workspace, rule, unowned_lead)

        # Somebody claims the lead between scheduling and execution.
        unowned_lead.owner = sales_rep.user
        unowned_lead.save(update_fields=["owner"])

        outcome, detail = execute_job(job)

    assert outcome == JobOutcome.CANCELLED
    assert "no longer hold" in detail
    assert Notification.objects.count() == 0


def test_an_action_removed_from_the_rule_cancels(workspace, unowned_lead):
    with workspace_context(workspace.id):
        rule = _rule(workspace, actions=[{"type": "notify_manager"}])
        # Queued against an action index the rule no longer has.
        job = _job_for(workspace, rule, unowned_lead, action_index=5)

        outcome, detail = execute_job(job)

    assert outcome == JobOutcome.CANCELLED
    assert "no longer exists" in detail


def test_a_repeat_execution_is_suppressed_not_repeated(workspace, unowned_lead):
    """The unique RuleExecution key turns at-least-once delivery into
    exactly-once effects (section 8.2)."""
    with workspace_context(workspace.id):
        rule = _rule(workspace, actions=[{"type": "notify_manager", "title": "Alert"}])
        job = _job_for(workspace, rule, unowned_lead)

        first, _ = execute_job(job)
        second, detail = execute_job(job)

        executions = RuleExecution.objects.filter(rule=rule).count()

    assert first == JobOutcome.SUCCEEDED
    assert second == JobOutcome.SUCCEEDED
    assert "duplicate suppressed" in detail
    # One effect, recorded once, despite two executions.
    assert executions == 1


def test_invalid_stored_configuration_fails_rather_than_acting(workspace, unowned_lead):
    """A rule that no longer validates must not be guessed at."""
    with workspace_context(workspace.id):
        rule = _rule(workspace, actions=[{"type": "notify_manager"}])
        job = _job_for(workspace, rule, unowned_lead)

        # Something outside the allowed action set.
        RuleDefinition.objects.filter(pk=rule.pk).update(actions=[{"type": "delete_everything"}])
        rule.refresh_from_db()
        job.rule = rule

        outcome, detail = execute_job(job)

    assert outcome == JobOutcome.FAILED
    assert "invalid" in detail.lower()


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def test_create_task_action_produces_a_task_for_the_owner(workspace, sales_rep, owned_lead):
    with workspace_context(workspace.id):
        rule = _rule(
            workspace,
            actions=[
                {
                    "type": "create_task",
                    "title": "Chase this lead",
                    "due_in": {"working_hours": 4},
                }
            ],
        )
        job = _job_for(workspace, rule, owned_lead)

        outcome, detail = execute_job(job)
        task = Task.objects.get(lead=owned_lead)

    assert outcome == JobOutcome.SUCCEEDED
    assert "Created task" in detail
    assert task.owner_id == sales_rep.user_id
    assert task.origin == "rule"
    assert task.status == TaskStatus.OPEN
    # A rule-created deadline is working time, on the workspace calendar.
    assert task.due_at > timezone.now()


def test_create_task_skips_a_suspended_owner(workspace, owner, sales_rep, owned_lead):
    """An inactive employee must not receive a new automatic assignment."""
    from modules.identity import services as identity_services

    with workspace_context(workspace.id):
        identity_services.suspend_member(
            actor_membership=owner,
            actor=owner.user,
            membership_id=sales_rep.id,
            reason="On leave",
        )
        rule = _rule(workspace, actions=[{"type": "create_task", "title": "Chase"}])
        job = _job_for(workspace, rule, owned_lead)

        outcome, detail = execute_job(job)

    assert outcome == JobOutcome.SUCCEEDED
    assert "No eligible owner" in detail
    assert Task.objects.filter(lead=owned_lead).count() == 0


def test_assign_owner_action_claims_an_unowned_lead(workspace, sales_rep, unowned_lead):
    with workspace_context(workspace.id):
        rule = _rule(workspace, actions=[{"type": "assign_owner"}])
        job = _job_for(workspace, rule, unowned_lead)

        outcome, detail = execute_job(job)
        unowned_lead.refresh_from_db()

    assert outcome == JobOutcome.SUCCEEDED
    assert "Assigned lead" in detail
    assert unowned_lead.owner_id is not None


def test_assign_owner_leaves_an_already_owned_lead_alone(workspace, owned_lead):
    original = owned_lead.owner_id
    with workspace_context(workspace.id):
        rule = _rule(workspace, actions=[{"type": "assign_owner"}])
        job = _job_for(workspace, rule, owned_lead)

        outcome, detail = execute_job(job)
        owned_lead.refresh_from_db()

    assert outcome == JobOutcome.SUCCEEDED
    assert "already has an owner" in detail
    assert owned_lead.owner_id == original


def test_assign_owner_finds_nobody_when_everyone_is_unavailable(
    workspace, sales_manager, sales_rep, unowned_lead
):
    """Better to leave a lead unassigned than to assign it to somebody away."""
    with workspace_context(workspace.id):
        for membership in (sales_manager, sales_rep):
            membership.is_available_for_assignment = False
            membership.save(update_fields=["is_available_for_assignment"])

        rule = _rule(workspace, actions=[{"type": "assign_owner"}])
        job = _job_for(workspace, rule, unowned_lead)

        outcome, detail = execute_job(job)
        unowned_lead.refresh_from_db()

    assert outcome == JobOutcome.SUCCEEDED
    assert "No eligible assignee" in detail
    assert unowned_lead.owner_id is None


def test_notify_owner_addresses_the_record_owner(workspace, sales_rep, owned_lead):
    with workspace_context(workspace.id):
        rule = _rule(
            workspace,
            actions=[{"type": "notify_owner", "title": "Your lead is waiting"}],
        )
        job = _job_for(workspace, rule, owned_lead)

        execute_job(job)
        alert = Notification.objects.get()

    assert alert.recipient_id == sales_rep.user_id
    # Every alert explains why it appeared.
    assert alert.cause


def test_notify_owner_does_nothing_for_an_unowned_record(workspace, unowned_lead):
    with workspace_context(workspace.id):
        rule = _rule(workspace, actions=[{"type": "notify_owner", "title": "Hi"}])
        job = _job_for(workspace, rule, unowned_lead)

        outcome, detail = execute_job(job)

    assert outcome == JobOutcome.SUCCEEDED
    assert "No eligible recipient" in detail
    assert Notification.objects.count() == 0


def test_a_second_alert_for_the_same_cause_is_suppressed(workspace, sales_rep, owned_lead):
    """One unresolved alert per cause (rule A01)."""
    with workspace_context(workspace.id):
        rule_one = _rule(workspace, actions=[{"type": "notify_owner", "title": "A"}])
        execute_job(_job_for(workspace, rule_one, owned_lead))

        # A different event, same rule template and version, same lead.
        second = _job_for(workspace, rule_one, owned_lead, action_index=1)
        second.payload["action_index"] = 0
        second.save(update_fields=["payload"])
        outcome, detail = execute_job(second)

    assert outcome == JobOutcome.SUCCEEDED
    assert "already exists" in detail
    assert Notification.objects.count() == 1


# ---------------------------------------------------------------------------
# Loop control (section 8.3)
# ---------------------------------------------------------------------------


def test_an_event_at_the_chain_depth_limit_schedules_nothing(workspace, unowned_lead, settings):
    """A rule chain must terminate, whatever the configuration says."""
    with workspace_context(workspace.id):
        _rule(workspace, actions=[{"type": "notify_manager"}])
        deep = OutboxEvent.objects.create(
            workspace=workspace,
            event_type="lead.created",
            aggregate_type="lead",
            aggregate_id=unowned_lead.id,
            chain_depth=settings.RULE_MAX_CHAIN_DEPTH,
        )

        assert plan_jobs_for_event(deep) == []


def test_a_shallow_event_does_schedule(workspace, unowned_lead):
    with workspace_context(workspace.id):
        _rule(workspace, actions=[{"type": "notify_manager"}])
        shallow = OutboxEvent.objects.create(
            workspace=workspace,
            event_type="lead.created",
            aggregate_type="lead",
            aggregate_id=unowned_lead.id,
            chain_depth=0,
        )
        plans = plan_jobs_for_event(shallow)

    assert len(plans) == 1
    assert plans[0].job_type == "rule:notify_manager"


def test_only_rules_matching_the_trigger_are_planned(workspace, unowned_lead):
    with workspace_context(workspace.id):
        _rule(workspace, actions=[{"type": "notify_manager"}])  # lead.created
        unrelated = OutboxEvent.objects.create(
            workspace=workspace,
            event_type="task.completed",
            aggregate_type="lead",
            aggregate_id=unowned_lead.id,
        )
        assert plan_jobs_for_event(unrelated) == []


def test_a_job_with_an_unknown_aggregate_type_cancels(workspace, unowned_lead):
    """Defensive: a payload the engine cannot resolve must not act blindly."""
    with workspace_context(workspace.id):
        rule = _rule(workspace, actions=[{"type": "notify_manager"}])
        job = _job_for(workspace, rule, unowned_lead)
        job.payload["aggregate_type"] = "something_else"
        job.save(update_fields=["payload"])

        outcome, _ = execute_job(job)

    assert outcome == JobOutcome.CANCELLED
