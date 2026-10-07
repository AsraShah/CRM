"""TEST05 / TEST06 - activity evidence and follow-up handling (CRM04, CRM05)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from modules.common.exceptions import ValidationFailed, VersionConflict
from modules.common.tenancy import workspace_context
from modules.crm import services as crm_services
from modules.crm.models import ActivityKind, EvidenceType
from modules.work import services as work_services
from modules.work.models import TaskStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def contact(workspace, contact_factory):
    with workspace_context(workspace.id):
        yield contact_factory(workspace, display_name="Prospect Ltd")


# ---------------------------------------------------------------------------
# Evidence labelling (CRM04)
# ---------------------------------------------------------------------------


def test_manual_entry_is_always_self_reported(workspace, sales_rep, contact):
    """CRM04 acceptance: a manual "call completed" is never shown as verified."""
    with workspace_context(workspace.id):
        activity = crm_services.record_activity(
            membership=sales_rep,
            actor=sales_rep.user,
            contact=contact,
            kind=ActivityKind.CALL,
            occurred_at=timezone.now() - timedelta(hours=1),
            outcome="Call completed, positive",
        )
    assert activity.evidence_type == EvidenceType.SELF_REPORTED
    assert activity.provider_reference == ""


def test_occurred_and_recorded_times_are_both_kept(workspace, sales_rep, contact):
    """Keeping both is what reveals a week of calls typed in on Friday."""
    occurred = timezone.now() - timedelta(days=3)
    with workspace_context(workspace.id):
        activity = crm_services.record_activity(
            membership=sales_rep,
            actor=sales_rep.user,
            contact=contact,
            kind=ActivityKind.CALL,
            occurred_at=occurred,
        )
    assert activity.occurred_at == occurred
    assert activity.recorded_at > occurred


def test_future_activity_is_refused(workspace, sales_rep, contact):
    with workspace_context(workspace.id), pytest.raises(ValidationFailed):
        crm_services.record_activity(
            membership=sales_rep,
            actor=sales_rep.user,
            contact=contact,
            kind=ActivityKind.CALL,
            occurred_at=timezone.now() + timedelta(hours=2),
        )


def test_correction_appends_and_preserves_the_original(workspace, sales_rep, contact):
    """CRM04 acceptance: an edited outcome leaves a correction trail."""
    with workspace_context(workspace.id):
        original = crm_services.record_activity(
            membership=sales_rep,
            actor=sales_rep.user,
            contact=contact,
            kind=ActivityKind.CALL,
            occurred_at=timezone.now() - timedelta(hours=2),
            outcome="Said yes",
        )
        correction = crm_services.correct_activity(
            membership=sales_rep,
            actor=sales_rep.user,
            activity_id=original.id,
            outcome="Actually said they would think about it",
            reason="Misheard on the call",
        )
        original.refresh_from_db()

    assert correction.corrects_id == original.id
    # The original is untouched, not overwritten.
    assert original.outcome == "Said yes"


def test_correction_requires_a_reason(workspace, sales_rep, contact):
    with workspace_context(workspace.id):
        original = crm_services.record_activity(
            membership=sales_rep,
            actor=sales_rep.user,
            contact=contact,
            kind=ActivityKind.NOTE,
            occurred_at=timezone.now() - timedelta(minutes=5),
            outcome="Note",
        )
        with pytest.raises(ValidationFailed):
            crm_services.correct_activity(
                membership=sales_rep,
                actor=sales_rep.user,
                activity_id=original.id,
                outcome="Changed",
                reason="   ",
            )


# ---------------------------------------------------------------------------
# Tasks (CRM05)
# ---------------------------------------------------------------------------


def test_completion_requires_a_next_action_or_a_stop_reason(workspace, sales_rep, contact):
    """CRM05 acceptance: completing offers the next action or an explicit stop."""
    with workspace_context(workspace.id):
        task = work_services.create_task(
            membership=sales_rep,
            actor=sales_rep.user,
            title="Call the prospect",
            due_at=timezone.now() + timedelta(hours=1),
            contact=contact,
        )
        # `match` tests the message; the field name lives in field_errors.
        with pytest.raises(ValidationFailed, match="Record the next action") as exc:
            work_services.complete_task(
                membership=sales_rep,
                actor=sales_rep.user,
                task_id=task.id,
                outcome="Spoke to them",
                expected_version=task.version,
            )
        assert "stop_reason" in exc.value.field_errors


def test_completion_with_a_next_action_creates_the_follow_up(workspace, sales_rep, contact):
    with workspace_context(workspace.id):
        task = work_services.create_task(
            membership=sales_rep,
            actor=sales_rep.user,
            title="Call the prospect",
            due_at=timezone.now() + timedelta(hours=1),
            contact=contact,
        )
        completed, next_task = work_services.complete_task(
            membership=sales_rep,
            actor=sales_rep.user,
            task_id=task.id,
            outcome="Interested; wants a proposal",
            expected_version=task.version,
            next_action_title="Send the proposal",
            next_action_due_at=timezone.now() + timedelta(days=1),
        )

    assert completed.status == TaskStatus.COMPLETED
    assert next_task is not None
    assert next_task.title == "Send the proposal"


def test_reschedule_preserves_the_original_deadline(workspace, sales_rep, contact):
    """CRM05 acceptance: a changed deadline does not erase the missed one."""
    original_due = timezone.now() + timedelta(hours=1)
    with workspace_context(workspace.id):
        task = work_services.create_task(
            membership=sales_rep,
            actor=sales_rep.user,
            title="Call the prospect",
            due_at=original_due,
            contact=contact,
        )
        moved = work_services.reschedule_task(
            membership=sales_rep,
            actor=sales_rep.user,
            task_id=task.id,
            new_due_at=timezone.now() + timedelta(days=2),
            reason="Prospect asked to postpone",
            expected_version=task.version,
        )
        history = list(moved.reschedules.all())

    assert moved.original_due_at == original_due
    assert moved.due_at != original_due
    assert moved.reschedule_count == 1
    assert history[0].previous_due_at == original_due
    assert history[0].reason == "Prospect asked to postpone"


def test_reschedule_requires_a_reason(workspace, sales_rep, contact):
    with workspace_context(workspace.id):
        task = work_services.create_task(
            membership=sales_rep,
            actor=sales_rep.user,
            title="Call",
            due_at=timezone.now() + timedelta(hours=1),
            contact=contact,
        )
        with pytest.raises(ValidationFailed):
            work_services.reschedule_task(
                membership=sales_rep,
                actor=sales_rep.user,
                task_id=task.id,
                new_due_at=timezone.now() + timedelta(days=1),
                reason="",
                expected_version=task.version,
            )


def test_completing_twice_is_idempotent(workspace, sales_rep, contact):
    """A duplicate click must not produce a duplicate business action."""
    with workspace_context(workspace.id):
        task = work_services.create_task(
            membership=sales_rep,
            actor=sales_rep.user,
            title="Call",
            due_at=timezone.now() + timedelta(hours=1),
            contact=contact,
        )
        first, _ = work_services.complete_task(
            membership=sales_rep,
            actor=sales_rep.user,
            task_id=task.id,
            outcome="Done",
            expected_version=task.version,
            stop_reason="No further action needed",
        )
        second, next_task = work_services.complete_task(
            membership=sales_rep,
            actor=sales_rep.user,
            task_id=task.id,
            outcome="Done again",
            expected_version=first.version,
            stop_reason="No further action needed",
        )

    assert second.completed_at == first.completed_at
    assert next_task is None


def test_stale_version_is_refused(workspace, sales_rep, contact):
    with workspace_context(workspace.id):
        task = work_services.create_task(
            membership=sales_rep,
            actor=sales_rep.user,
            title="Call",
            due_at=timezone.now() + timedelta(hours=1),
            contact=contact,
        )
        work_services.reschedule_task(
            membership=sales_rep,
            actor=sales_rep.user,
            task_id=task.id,
            new_due_at=timezone.now() + timedelta(days=1),
            reason="Postponed",
            expected_version=task.version,
        )
        with pytest.raises(VersionConflict):
            work_services.complete_task(
                membership=sales_rep,
                actor=sales_rep.user,
                task_id=task.id,
                outcome="Done",
                expected_version=task.version,  # now stale
                stop_reason="Finished",
            )


def test_a_rep_cannot_complete_another_reps_task(workspace, sales_rep, other_rep, contact):
    from modules.common.exceptions import NotAuthorized

    with workspace_context(workspace.id):
        task = work_services.create_task(
            membership=sales_rep,
            actor=sales_rep.user,
            title="Call",
            due_at=timezone.now() + timedelta(hours=1),
            contact=contact,
        )
        with pytest.raises(NotAuthorized):
            work_services.complete_task(
                membership=other_rep,
                actor=other_rep.user,
                task_id=task.id,
                outcome="Done",
                expected_version=task.version,
                stop_reason="Finished",
            )
