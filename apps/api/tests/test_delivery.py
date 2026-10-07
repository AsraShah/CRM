"""TEST09 - milestone transitions, dependencies and permissions (CRM08)."""

from __future__ import annotations

import pytest

from modules.common.exceptions import (
    NotAuthorized,
    TransitionNotAllowed,
    ValidationFailed,
)
from modules.common.tenancy import workspace_context
from modules.crm.models import Client, ClientStatus
from modules.identity.models import Role
from modules.work import delivery
from modules.work.models import (
    Milestone,
    MilestoneStatus,
    Project,
    ProjectStatus,
    ScopeChange,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def delivery_manager(workspace):
    from tests.conftest import _make_member

    return _make_member(workspace, "delivery@scalevexo.test", Role.DELIVERY_MANAGER)


@pytest.fixture
def delivery_employee(workspace):
    from tests.conftest import _make_member

    return _make_member(workspace, "dev@scalevexo.test", Role.DELIVERY_EMPLOYEE)


@pytest.fixture
def project(workspace, delivery_manager, contact_factory):
    with workspace_context(workspace.id):
        contact = contact_factory(workspace, display_name="Acme Ltd")
        client = Client.objects.create(
            workspace=workspace,
            contact=contact,
            display_name="Acme Ltd",
            status=ClientStatus.ACCEPTED,
            delivery_owner=delivery_manager.user,
        )
        yield Project.objects.create(
            workspace=workspace,
            client=client,
            name="Acme onboarding",
            status=ProjectStatus.IN_PROGRESS,
            owner=delivery_manager.user,
        )


def _milestone(workspace, project, name: str, **kwargs) -> Milestone:
    return Milestone.objects.create(
        workspace=workspace,
        project=project,
        name=name,
        status=kwargs.pop("status", MilestoneStatus.IN_PROGRESS),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Submission versus acceptance
# ---------------------------------------------------------------------------


def test_submission_requires_evidence(workspace, project, delivery_employee):
    with workspace_context(workspace.id):
        milestone = _milestone(workspace, project, "Build the homepage")
        with pytest.raises(ValidationFailed):
            delivery.submit_milestone(
                membership=delivery_employee,
                actor=delivery_employee.user,
                milestone_id=milestone.id,
                evidence="   ",
                expected_version=milestone.version,
            )


def test_submission_is_not_acceptance(workspace, project, delivery_employee):
    """An employee's claim of completion is not delivery of the work (CRM08)."""
    with workspace_context(workspace.id):
        milestone = _milestone(workspace, project, "Build the homepage")
        submitted = delivery.submit_milestone(
            membership=delivery_employee,
            actor=delivery_employee.user,
            milestone_id=milestone.id,
            evidence="Deployed to staging, link in the ticket.",
            expected_version=milestone.version,
        )

    assert submitted.status == MilestoneStatus.IN_REVIEW
    assert submitted.submitted_by_id == delivery_employee.user_id
    # Nothing has been accepted.
    assert submitted.accepted_at is None
    assert submitted.accepted_by_id is None


def test_an_employee_cannot_accept_work(workspace, project, delivery_employee):
    with workspace_context(workspace.id):
        milestone = _milestone(workspace, project, "Build the homepage")
        delivery.submit_milestone(
            membership=delivery_employee,
            actor=delivery_employee.user,
            milestone_id=milestone.id,
            evidence="Done.",
            expected_version=milestone.version,
        )
        milestone.refresh_from_db()

        with pytest.raises(NotAuthorized):
            delivery.accept_milestone(
                membership=delivery_employee,
                actor=delivery_employee.user,
                milestone_id=milestone.id,
                expected_version=milestone.version,
            )


def test_acceptance_records_the_reviewer(workspace, project, delivery_employee, delivery_manager):
    with workspace_context(workspace.id):
        milestone = _milestone(workspace, project, "Build the homepage")
        delivery.submit_milestone(
            membership=delivery_employee,
            actor=delivery_employee.user,
            milestone_id=milestone.id,
            evidence="Deployed to staging.",
            expected_version=milestone.version,
        )
        milestone.refresh_from_db()

        accepted = delivery.accept_milestone(
            membership=delivery_manager,
            actor=delivery_manager.user,
            milestone_id=milestone.id,
            expected_version=milestone.version,
            note="Checked on staging.",
        )

    assert accepted.status == MilestoneStatus.ACCEPTED
    assert accepted.submitted_by_id == delivery_employee.user_id
    assert accepted.accepted_by_id == delivery_manager.user_id


def test_work_cannot_be_accepted_without_being_submitted(workspace, project, delivery_manager):
    with workspace_context(workspace.id):
        milestone = _milestone(workspace, project, "Build the homepage")
        with pytest.raises(TransitionNotAllowed):
            delivery.accept_milestone(
                membership=delivery_manager,
                actor=delivery_manager.user,
                milestone_id=milestone.id,
                expected_version=milestone.version,
            )


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def test_dependent_milestone_cannot_be_accepted_first(
    workspace, project, delivery_manager, delivery_employee
):
    """CRM08 acceptance: not before its predecessor, unless overridden."""
    with workspace_context(workspace.id):
        first = _milestone(workspace, project, "Design approved")
        second = _milestone(workspace, project, "Build complete")
        delivery.add_dependency(
            membership=delivery_manager,
            actor=delivery_manager.user,
            milestone_id=second.id,
            depends_on_id=first.id,
        )

        delivery.submit_milestone(
            membership=delivery_employee,
            actor=delivery_employee.user,
            milestone_id=second.id,
            evidence="Built.",
            expected_version=second.version,
        )
        second.refresh_from_db()

        with pytest.raises(TransitionNotAllowed) as exc:
            delivery.accept_milestone(
                membership=delivery_manager,
                actor=delivery_manager.user,
                milestone_id=second.id,
                expected_version=second.version,
            )
    assert exc.value.code == "unmet_dependencies"
    assert exc.value.extra["unmet"][0]["name"] == "Design approved"


def test_manager_override_is_recorded_with_its_reason(
    workspace, project, delivery_manager, delivery_employee
):
    """Breaking the rule must leave a trace; that is the point of the rule."""
    with workspace_context(workspace.id):
        first = _milestone(workspace, project, "Design approved")
        second = _milestone(workspace, project, "Build complete")
        delivery.add_dependency(
            membership=delivery_manager,
            actor=delivery_manager.user,
            milestone_id=second.id,
            depends_on_id=first.id,
        )
        delivery.submit_milestone(
            membership=delivery_employee,
            actor=delivery_employee.user,
            milestone_id=second.id,
            evidence="Built.",
            expected_version=second.version,
        )
        second.refresh_from_db()

        # An override without a reason is refused.
        with pytest.raises(ValidationFailed):
            delivery.accept_milestone(
                membership=delivery_manager,
                actor=delivery_manager.user,
                milestone_id=second.id,
                expected_version=second.version,
                override_dependencies=True,
                override_reason="",
            )

        accepted = delivery.accept_milestone(
            membership=delivery_manager,
            actor=delivery_manager.user,
            milestone_id=second.id,
            expected_version=second.version,
            override_dependencies=True,
            override_reason="Client approved the design verbally on the call.",
        )

    assert accepted.status == MilestoneStatus.ACCEPTED
    assert accepted.dependency_override_by_id == delivery_manager.user_id
    assert "verbally" in accepted.dependency_override_reason


def test_dependency_cycles_are_refused(workspace, project, delivery_manager):
    """A cycle would make both milestones permanently unacceptable."""
    with workspace_context(workspace.id):
        first = _milestone(workspace, project, "A")
        second = _milestone(workspace, project, "B")
        third = _milestone(workspace, project, "C")

        delivery.add_dependency(
            membership=delivery_manager,
            actor=delivery_manager.user,
            milestone_id=second.id,
            depends_on_id=first.id,
        )
        delivery.add_dependency(
            membership=delivery_manager,
            actor=delivery_manager.user,
            milestone_id=third.id,
            depends_on_id=second.id,
        )

        # A -> B -> C, so making A depend on C closes the loop.
        with pytest.raises(ValidationFailed) as exc:
            delivery.add_dependency(
                membership=delivery_manager,
                actor=delivery_manager.user,
                milestone_id=first.id,
                depends_on_id=third.id,
            )
    assert exc.value.code == "dependency_cycle"


def test_self_dependency_is_refused(workspace, project, delivery_manager):
    with workspace_context(workspace.id):
        milestone = _milestone(workspace, project, "A")
        with pytest.raises(ValidationFailed):
            delivery.add_dependency(
                membership=delivery_manager,
                actor=delivery_manager.user,
                milestone_id=milestone.id,
                depends_on_id=milestone.id,
            )


# ---------------------------------------------------------------------------
# Reopening and blocking
# ---------------------------------------------------------------------------


def test_reopening_preserves_the_earlier_evidence(
    workspace, project, delivery_manager, delivery_employee
):
    """CRM08 acceptance: reopening preserves the earlier evidence and reason."""
    with workspace_context(workspace.id):
        milestone = _milestone(workspace, project, "Build complete")
        delivery.submit_milestone(
            membership=delivery_employee,
            actor=delivery_employee.user,
            milestone_id=milestone.id,
            evidence="Original evidence: deployed build 41.",
            expected_version=milestone.version,
        )
        milestone.refresh_from_db()
        delivery.accept_milestone(
            membership=delivery_manager,
            actor=delivery_manager.user,
            milestone_id=milestone.id,
            expected_version=milestone.version,
        )
        milestone.refresh_from_db()

        reopened = delivery.reopen_milestone(
            membership=delivery_manager,
            actor=delivery_manager.user,
            milestone_id=milestone.id,
            reason="Client found a broken contact form.",
            expected_version=milestone.version,
        )

    assert reopened.status == MilestoneStatus.IN_PROGRESS
    # The original evidence and the reason for reopening both survive.
    assert "Original evidence: deployed build 41." in reopened.evidence
    assert "broken contact form" in reopened.evidence


def test_blocking_requires_a_reason_and_a_next_owner(workspace, project, delivery_manager):
    with workspace_context(workspace.id):
        milestone = _milestone(workspace, project, "Awaiting client assets")

        with pytest.raises(ValidationFailed):
            delivery.block_milestone(
                membership=delivery_manager,
                actor=delivery_manager.user,
                milestone_id=milestone.id,
                blocked_reason="",
                next_owner_id=delivery_manager.user_id,
                expected_version=milestone.version,
            )

        blocked = delivery.block_milestone(
            membership=delivery_manager,
            actor=delivery_manager.user,
            milestone_id=milestone.id,
            blocked_reason="Client has not sent brand assets.",
            next_owner_id=delivery_manager.user_id,
            expected_version=milestone.version,
        )

    assert blocked.status == MilestoneStatus.BLOCKED
    assert blocked.blocked_next_owner_id == delivery_manager.user_id


def test_blocked_work_can_be_resumed(workspace, project, delivery_manager, delivery_employee):
    """BLOCKED -> IN_PROGRESS is a permitted transition, so something must perform it.

    Before this, start_milestone accepted only PLANNED or READY: once blocked, a
    milestone could never move again, and its project stayed blocked for good.
    """
    with workspace_context(workspace.id):
        milestone = _milestone(workspace, project, "Awaiting client assets")
        blocked = delivery.block_milestone(
            membership=delivery_manager,
            actor=delivery_manager.user,
            milestone_id=milestone.id,
            blocked_reason="Client has not sent brand assets.",
            next_owner_id=delivery_manager.user_id,
            expected_version=milestone.version,
        )

        resumed = delivery.start_milestone(
            membership=delivery_employee,
            actor=delivery_employee.user,
            milestone_id=milestone.id,
            expected_version=blocked.version,
        )
        project.refresh_from_db()

    assert resumed.status == MilestoneStatus.IN_PROGRESS
    # The block is over, so its reason and next owner no longer describe it.
    assert resumed.blocked_reason == ""
    assert resumed.blocked_next_owner_id is None
    assert project.status != "blocked"


def test_project_status_follows_its_milestones(
    workspace, project, delivery_manager, delivery_employee
):
    """A project cannot report complete while work remains open beneath it."""
    with workspace_context(workspace.id):
        one = _milestone(workspace, project, "One")
        two = _milestone(workspace, project, "Two")

        for milestone in (one, two):
            delivery.submit_milestone(
                membership=delivery_employee,
                actor=delivery_employee.user,
                milestone_id=milestone.id,
                evidence="Done.",
                expected_version=milestone.version,
            )
            milestone.refresh_from_db()

        delivery.accept_milestone(
            membership=delivery_manager,
            actor=delivery_manager.user,
            milestone_id=one.id,
            expected_version=one.version,
        )
        project.refresh_from_db()
        assert project.status == ProjectStatus.IN_PROGRESS

        two.refresh_from_db()
        delivery.accept_milestone(
            membership=delivery_manager,
            actor=delivery_manager.user,
            milestone_id=two.id,
            expected_version=two.version,
        )
        project.refresh_from_db()

    assert project.status == ProjectStatus.COMPLETED
    assert project.completed_at is not None


# ---------------------------------------------------------------------------
# Scope changes
# ---------------------------------------------------------------------------


def test_scope_changes_and_defects_are_recorded_separately(workspace, project, delivery_manager):
    """Conflating them destroys the only honest answer to "why did it overrun?"."""
    with workspace_context(workspace.id):
        delivery.record_scope_change(
            membership=delivery_manager,
            actor=delivery_manager.user,
            project_id=project.id,
            kind="scope_change",
            description="Client asked for a second language.",
            commercial_impact="Additional 15 hours, quoted separately.",
        )
        delivery.record_scope_change(
            membership=delivery_manager,
            actor=delivery_manager.user,
            project_id=project.id,
            kind="defect",
            description="Contact form did not submit on Safari.",
        )

        kinds = set(ScopeChange.objects.filter(project=project).values_list("kind", flat=True))

    assert kinds == {"scope_change", "defect"}
