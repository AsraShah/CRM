"""Business rules that the journey tests reach only incidentally.

These cover the branches that the end-to-end paths happen not to take: the
comparison operators a rule author can use, the loop and cooldown limits, the
retry ladder, the lead qualification states, and the operational reports.

Each of these is a rule the specification states explicitly. An untested rule
is a rule that holds by coincidence.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from modules.automation import engine
from modules.automation.models import Job, JobState, RuleDefinition, RuleTemplate
from modules.automation.schemas import Condition
from modules.automation.worker import _handle_failure, record_heartbeat
from modules.common.exceptions import NotAuthorized, ValidationFailed
from modules.common.tenancy import workspace_context
from modules.crm import services as crm_services
from modules.crm.models import ActivityKind, LeadStatus, OpportunityStage
from modules.reporting import metrics
from modules.work import services as work_services

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Rule condition operators (section 8.1)
# ---------------------------------------------------------------------------


class _Thing:
    """Stand-in for an entity in the evaluation context."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _evaluate(field: str, op: str, value, actual) -> bool:
    condition = Condition(field=field, op=op, value=value)
    return engine.evaluate_condition(condition, {"task": _Thing(**{field.split(".")[1]: actual})})


def test_every_allowed_operator_behaves():
    assert _evaluate("task.status", "eq", "open", "open") is True
    assert _evaluate("task.status", "eq", "open", "completed") is False
    assert _evaluate("task.status", "neq", "open", "completed") is True
    assert _evaluate("task.kind", "in", ["call", "email"], "call") is True
    assert _evaluate("task.kind", "in", ["call", "email"], "meeting") is False
    assert _evaluate("task.reschedule_count", "gt", 2, 3) is True
    assert _evaluate("task.reschedule_count", "gt", 2, 1) is False
    assert _evaluate("task.reschedule_count", "lt", 2, 1) is True


def test_ordering_comparisons_against_unknown_are_false_not_errors():
    """A NULL field must not crash the worker, and must not count as a match."""
    assert _evaluate("task.due_at", "gt", 5, None) is False
    assert _evaluate("task.due_at", "lt", 5, None) is False


def test_uuid_values_compare_across_representations():
    """Stored JSON gives a string; the ORM gives a UUID. They must agree."""
    owner = uuid.uuid4()
    assert _evaluate("task.owner_id", "eq", str(owner), owner) is True
    assert _evaluate("task.owner_id", "eq", str(uuid.uuid4()), owner) is False


def test_is_empty_treats_blank_and_missing_alike():
    assert _evaluate("task.status", "is_empty", None, "") is True
    assert _evaluate("task.status", "is_empty", None, None) is True
    assert _evaluate("task.status", "is_empty", None, []) is True
    assert _evaluate("task.status", "is_empty", None, "open") is False
    # Explicitly asking for "not empty".
    assert _evaluate("task.status", "is_empty", False, "open") is True


def test_all_conditions_must_hold():
    """There is no OR in the Release 1 schema; conditions are conjunctive."""
    context = {"task": _Thing(status="open", kind="call")}
    conditions = [
        Condition(field="task.status", op="eq", value="open"),
        Condition(field="task.kind", op="eq", value="email"),
    ]
    assert engine.evaluate_conditions(conditions, context) is False
    assert engine.evaluate_conditions(conditions[:1], context) is True


def test_no_conditions_means_the_trigger_alone_decides():
    assert engine.evaluate_conditions([], {"task": _Thing(status="open")}) is True


# ---------------------------------------------------------------------------
# Rule simulation (section 8.3)
# ---------------------------------------------------------------------------


@pytest.fixture
def overdue_rule(workspace):
    with workspace_context(workspace.id):
        return RuleDefinition.objects.create(
            workspace=workspace,
            template=RuleTemplate.OVERDUE_FOLLOWUP,
            name="A03 Overdue follow-up",
            rule_version=1,
            enabled=True,
            trigger="task.due",
            conditions=[{"field": "task.status", "op": "eq", "value": "open"}],
            actions=[{"type": "notify_owner", "channel": "in_app", "title": "Overdue"}],
            delay={"working_hours": 2},
            limits={"max_actions": 1, "cooldown_minutes": 60, "max_chain_depth": 3},
            explanation="A follow-up passed its due time.",
        )


def test_simulation_shows_the_match_without_acting(workspace, overdue_rule):
    """An administrator sees what a rule would do before enabling it."""
    with workspace_context(workspace.id):
        would_fire = engine.simulate(overdue_rule, {"task": _Thing(status="open")})
        would_not = engine.simulate(overdue_rule, {"task": _Thing(status="completed")})

    assert would_fire["would_fire"] is True
    assert would_fire["proposed_actions"] == ["notify_owner"]
    assert would_fire["conditions"][0]["actual"] == "open"

    assert would_not["would_fire"] is False
    # No actions are proposed when the rule would not fire.
    assert would_not["proposed_actions"] == []


def test_simulation_reports_each_condition_separately(workspace, overdue_rule):
    """ "Why did this not fire?" needs per-condition detail, not a verdict."""
    with workspace_context(workspace.id):
        result = engine.simulate(overdue_rule, {"task": _Thing(status="completed")})

    entry = result["conditions"][0]
    assert entry["field"] == "task.status"
    assert entry["matched"] is False
    assert entry["actual"] == "completed"


# ---------------------------------------------------------------------------
# Worker retry ladder (section 8.2)
# ---------------------------------------------------------------------------


def _job(workspace, **overrides) -> Job:
    with workspace_context(workspace.id):
        return Job.objects.create(
            workspace=workspace,
            job_type="rule:notify_owner",
            dedupe_key=f"task:{uuid.uuid4()}|evt|t@1|0:notify_owner",
            due_at=timezone.now() - timedelta(minutes=5),
            **overrides,
        )


def test_a_transient_failure_is_retried_with_backoff(workspace, settings):
    """1, 5 and 30 minutes with jitter, not an immediate hot loop."""
    job = _job(workspace, state=JobState.LEASED, lease_token=uuid.uuid4(), attempts=1)

    with workspace_context(workspace.id):
        _handle_failure(job, job.lease_token, "temporary provider error")
        job.refresh_from_db()

    assert job.state == JobState.PENDING
    assert job.due_at > timezone.now()
    # First rung is ~1 minute; jitter is 0.8-1.2x.
    assert job.due_at < timezone.now() + timedelta(minutes=2)
    assert "temporary" in job.last_error


def test_retries_are_exhausted_into_the_failed_queue(workspace, settings):
    """A job that keeps failing becomes visible work for an operator."""
    job = _job(
        workspace,
        state=JobState.LEASED,
        lease_token=uuid.uuid4(),
        attempts=settings.JOB_MAX_ATTEMPTS,
    )

    with workspace_context(workspace.id):
        _handle_failure(job, job.lease_token, "still failing")
        job.refresh_from_db()

    assert job.state == JobState.FAILED
    assert job.completed_at is not None
    # The lease is released so nothing looks like it is still running.
    assert job.lease_token is None


def test_a_stale_worker_cannot_record_a_failure_either(workspace):
    """Lease checking guards the failure path, not just the success path."""
    from modules.automation.worker import LeaseLost

    job = _job(workspace, state=JobState.LEASED, lease_token=uuid.uuid4(), attempts=1)

    with workspace_context(workspace.id), pytest.raises(LeaseLost):
        _handle_failure(job, uuid.uuid4(), "from a worker that lost its lease")


def test_heartbeat_records_liveness(workspace):
    """A silently dead worker is otherwise indistinguishable from a quiet queue."""
    from modules.automation.models import SchedulerHeartbeat

    record_heartbeat("worker", detail={"succeeded": 3})
    beat = SchedulerHeartbeat.objects.get(component="worker")

    assert beat.detail["succeeded"] == 3
    assert beat.last_beat_at is not None


# ---------------------------------------------------------------------------
# Lead qualification states (SVX-PRD-001 section 5.1)
# ---------------------------------------------------------------------------


@pytest.fixture
def lead(workspace, sales_manager, contact_factory):
    with workspace_context(workspace.id):
        contact = contact_factory(workspace, display_name="Prospect")
        yield crm_services.create_lead(
            membership=sales_manager,
            actor=sales_manager.user,
            contact=contact,
            owner=sales_manager.user,
        )


def test_nurture_requires_a_review_date(workspace, sales_manager, lead):
    """Otherwise "nurture" is indistinguishable from "forgotten"."""
    with workspace_context(workspace.id):
        with pytest.raises(ValidationFailed) as exc:
            crm_services.change_lead_status(
                membership=sales_manager,
                actor=sales_manager.user,
                lead_id=lead.id,
                new_status=LeadStatus.NURTURE,
                expected_version=lead.version,
            )
        assert "nurture_review_at" in exc.value.field_errors

        moved = crm_services.change_lead_status(
            membership=sales_manager,
            actor=sales_manager.user,
            lead_id=lead.id,
            new_status=LeadStatus.NURTURE,
            expected_version=lead.version,
            nurture_review_at=timezone.now() + timedelta(days=30),
        )
    assert moved.status == LeadStatus.NURTURE


def test_disqualification_requires_a_reason(workspace, sales_manager, lead):
    with workspace_context(workspace.id):
        with pytest.raises(ValidationFailed) as exc:
            crm_services.change_lead_status(
                membership=sales_manager,
                actor=sales_manager.user,
                lead_id=lead.id,
                new_status=LeadStatus.DISQUALIFIED,
                expected_version=lead.version,
            )
        assert "disqualified_reason" in exc.value.field_errors


def test_qualifying_stamps_the_moment(workspace, sales_manager, lead):
    with workspace_context(workspace.id):
        qualified = crm_services.change_lead_status(
            membership=sales_manager,
            actor=sales_manager.user,
            lead_id=lead.id,
            new_status=LeadStatus.QUALIFIED,
            expected_version=lead.version,
            need="Organic traffic has stalled for two quarters.",
            fit="We run six-month SEO retainers for agencies this size.",
        )
    assert qualified.qualified_at is not None


def test_reassignment_requires_manager_authority(workspace, sales_rep, lead):
    with workspace_context(workspace.id), pytest.raises(NotAuthorized):
        crm_services.assign_lead(
            membership=sales_rep,
            actor=sales_rep.user,
            lead_id=lead.id,
            owner_id=sales_rep.user_id,
            expected_version=lead.version,
        )


def test_reassignment_records_who_and_why(workspace, sales_manager, sales_rep, lead):
    """Managers transfer work with a recorded reason (CRM01)."""
    from modules.common.models import AuditEvent

    with workspace_context(workspace.id):
        moved = crm_services.assign_lead(
            membership=sales_manager,
            actor=sales_manager.user,
            lead_id=lead.id,
            owner_id=sales_rep.user_id,
            expected_version=lead.version,
            reason="Rebalancing the territory",
        )
        event = AuditEvent.objects.filter(entity_type="crm.Lead", entity_id=lead.id).latest(
            "occurred_at"
        )

    assert moved.owner_id == sales_rep.user_id
    assert moved.status == LeadStatus.ASSIGNED
    assert event.reason == "Rebalancing the territory"


def test_work_cannot_be_assigned_to_a_suspended_member(
    workspace, owner, sales_manager, sales_rep, lead
):
    """An inactive employee must not receive new automatic assignment."""
    from modules.identity import services as identity_services

    with workspace_context(workspace.id):
        identity_services.suspend_member(
            actor_membership=owner,
            actor=owner.user,
            membership_id=sales_rep.id,
            reason="On leave",
        )
        with pytest.raises(ValidationFailed) as exc:
            crm_services.assign_lead(
                membership=sales_manager,
                actor=sales_manager.user,
                lead_id=lead.id,
                owner_id=sales_rep.user_id,
                expected_version=lead.version,
            )
    assert "owner_id" in exc.value.field_errors


# ---------------------------------------------------------------------------
# Task next-action bookkeeping (CRM05)
# ---------------------------------------------------------------------------


def test_completing_a_task_clears_the_deals_next_action(workspace, sales_rep, contact_factory):
    """Rule A02 fires on deals with nothing scheduled, so this must be exact."""
    with workspace_context(workspace.id):
        contact = contact_factory(workspace)
        deal = crm_services.create_opportunity(
            membership=sales_rep,
            actor=sales_rep.user,
            contact=contact,
            service="Retainer",
        )
        task = work_services.create_task(
            membership=sales_rep,
            actor=sales_rep.user,
            title="Call them",
            due_at=timezone.now() + timedelta(hours=2),
            opportunity=deal,
            contact=contact,
        )
        deal.refresh_from_db()
        assert deal.next_action_at is not None

        work_services.complete_task(
            membership=sales_rep,
            actor=sales_rep.user,
            task_id=task.id,
            outcome="Spoke to them.",
            expected_version=task.version,
            stop_reason="Waiting on their board.",
        )
        deal.refresh_from_db()

    assert deal.next_action_at is None


def test_a_manager_can_assign_work_to_someone_else(
    workspace, sales_manager, sales_rep, contact_factory
):
    with workspace_context(workspace.id):
        contact = contact_factory(workspace)
        task = work_services.create_task(
            membership=sales_manager,
            actor=sales_manager.user,
            title="Follow this up",
            due_at=timezone.now() + timedelta(hours=4),
            owner=sales_rep.user,
            contact=contact,
        )
    assert task.owner_id == sales_rep.user_id
    assert task.created_by_id == sales_manager.user_id


def test_a_representative_cannot_assign_work_to_a_colleague(
    workspace, sales_rep, other_rep, contact_factory
):
    with workspace_context(workspace.id):
        contact = contact_factory(workspace)
        with pytest.raises(NotAuthorized):
            work_services.create_task(
                membership=sales_rep,
                actor=sales_rep.user,
                title="You do it",
                due_at=timezone.now() + timedelta(hours=4),
                owner=other_rep.user,
                contact=contact,
            )


def test_a_task_needs_a_title_and_a_due_time(workspace, sales_rep):
    with workspace_context(workspace.id):
        with pytest.raises(ValidationFailed):
            work_services.create_task(
                membership=sales_rep,
                actor=sales_rep.user,
                title="   ",
                due_at=timezone.now() + timedelta(hours=1),
            )
        with pytest.raises(ValidationFailed):
            work_services.create_task(
                membership=sales_rep,
                actor=sales_rep.user,
                title="No deadline",
                due_at=None,
            )


# ---------------------------------------------------------------------------
# Operational reports (CRM11)
# ---------------------------------------------------------------------------


@pytest.fixture
def reporting_fixture(workspace, sales_manager, contact_factory):
    """One won deal, one open deal and an activity, for the report shapes."""
    with workspace_context(workspace.id):
        contact = contact_factory(workspace, display_name="Reportable Ltd")

        won = crm_services.create_opportunity(
            membership=sales_manager,
            actor=sales_manager.user,
            contact=contact,
            service="Won work",
            amount=Decimal("9000.00"),
            currency="USD",
        )
        won.stage = OpportunityStage.WON
        won.accepted_scope_evidence = "SOW"
        won.commercial_reference = "INV-1"
        won.closed_at = timezone.now()
        won.save()

        crm_services.create_opportunity(
            membership=sales_manager,
            actor=sales_manager.user,
            contact=contact,
            service="Open work",
            amount=Decimal("4000.00"),
            currency="USD",
        )
        crm_services.record_activity(
            membership=sales_manager,
            actor=sales_manager.user,
            contact=contact,
            kind=ActivityKind.CALL,
            occurred_at=timezone.now() - timedelta(hours=2),
            outcome="Spoke to them.",
        )
        yield contact


def test_conversion_by_source_shows_its_denominator(workspace, owner, reporting_fixture):
    """A rate over five deals is noise; the reader must see the sample size."""
    with workspace_context(workspace.id):
        report = metrics.conversion_by_source(
            membership=owner,
            period_start=date.today() - timedelta(days=7),
            period_end=date.today(),
        )
    assert "caveat" in report
    for row in report["rows"]:
        # Both parts, not a bare percentage.
        assert "/" in row["won_per_lead"]


def test_stage_aging_distinguishes_unknown_age(workspace, owner, reporting_fixture):
    with workspace_context(workspace.id):
        report = metrics.stage_aging(membership=owner)

    discovery = report["stages"]["discovery"]
    assert discovery["total"] >= 1
    # "Age unknown" is its own bucket, not folded into zero days.
    assert "age_unknown" in discovery


def test_activity_evidence_split_is_all_self_reported_before_release_2(
    workspace, owner, reporting_fixture
):
    """No connector exists yet, so nothing can legitimately be confirmed."""
    with workspace_context(workspace.id):
        report = metrics.activity_evidence_split(
            membership=owner,
            period_start=date.today() - timedelta(days=7),
            period_end=date.today(),
        )
    assert report["self_reported"] >= 1
    assert report["provider_confirmed"] == 0
    assert "not evidence that it did" in report["interpretation"]


def test_delivery_health_reports_blockers(workspace, owner):
    with workspace_context(workspace.id):
        report = metrics.delivery_health(membership=owner)
    assert report["blocked_milestones"] == []
    assert report["reopened_tickets"] == 0
    assert report["dependency_overrides"] == 0


def test_pilot_measures_report_numerator_and_denominator(workspace, owner, reporting_fixture):
    """Proposed thresholds, with raw counts so they can be inspected."""
    with workspace_context(workspace.id):
        measures = metrics.pilot_measures(membership=owner)

    coverage = measures["next_action_coverage"]
    assert set(coverage) == {
        "numerator",
        "denominator",
        "percentage",
        "proposed_threshold",
    }
    assert "not performance results" in measures["note"]


def test_attention_counts_flag_a_deal_with_no_next_action(workspace, owner, reporting_fixture):
    with workspace_context(workspace.id):
        counts = metrics.attention_counts(membership=owner)
    assert counts["open_deals_without_next_action"] >= 1


def test_qualifying_requires_need_and_fit(workspace, sales_manager, lead):
    """SVX-PRD-001 section 5: qualification records need and fit."""
    with workspace_context(workspace.id), pytest.raises(ValidationFailed) as exc:
        crm_services.change_lead_status(
            membership=sales_manager,
            actor=sales_manager.user,
            lead_id=lead.id,
            new_status=LeadStatus.QUALIFIED,
            expected_version=lead.version,
            need="Needs more qualified inbound leads.",
        )
    assert "fit" in exc.value.field_errors
