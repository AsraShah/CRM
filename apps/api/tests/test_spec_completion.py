"""Behaviour the specifications state that had no implementation.

Each test names the requirement and the sentence it covers:

- CRM08: milestone cancellation "requires reason and impact"; a reviewer can
  send submitted work back; "projects shall contain milestones, tasks".
- CRM09: "tickets shall show waiting reasons, next action".
- CRM04: "follow-up creation is available from the activity form".
- CRM02: "merging requires review and preserves source references and history".
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from modules.common.tenancy import workspace_context
from modules.crm.models import Activity, Client, ClientStatus, Contact, Lead
from modules.identity.models import Role
from modules.work.models import Milestone, MilestoneStatus, Project, ProjectStatus, Task
from tests.conftest import _make_member

pytestmark = pytest.mark.django_db


def _api(membership) -> APIClient:
    api = APIClient()
    api.force_login(membership.user)
    return api


@pytest.fixture
def delivery_manager(workspace):
    return _make_member(workspace, "delivery@scalevexo.test", Role.DELIVERY_MANAGER)


@pytest.fixture
def delivery_employee(workspace):
    return _make_member(workspace, "dev@scalevexo.test", Role.DELIVERY_EMPLOYEE)


@pytest.fixture
def project(workspace, delivery_manager, contact_factory):
    with workspace_context(workspace.id):
        client = Client.objects.create(
            workspace=workspace,
            contact=contact_factory(workspace, display_name="Acme Ltd"),
            display_name="Acme Ltd",
            status=ClientStatus.ACCEPTED,
            delivery_owner=delivery_manager.user,
        )
        return Project.objects.create(
            workspace=workspace,
            client=client,
            name="Acme onboarding",
            status=ProjectStatus.IN_PROGRESS,
            owner=delivery_manager.user,
        )


def _milestone(workspace, project, status=MilestoneStatus.IN_PROGRESS, **extra) -> Milestone:
    with workspace_context(workspace.id):
        return Milestone.objects.create(
            workspace=workspace, project=project, name="Launch site", status=status, **extra
        )


# ---------------------------------------------------------------------------
# CRM08
# ---------------------------------------------------------------------------


def test_cancelling_a_milestone_needs_a_reason_and_its_impact(workspace, project, delivery_manager):
    milestone = _milestone(workspace, project)
    api = _api(delivery_manager)

    missing = api.post(
        f"/api/v1/milestones/{milestone.id}/cancel/",
        {"expected_version": milestone.version, "reason": "Client dropped it."},
        format="json",
    )
    assert missing.status_code == 400
    assert "impact" in missing.json()["field_errors"]

    done = api.post(
        f"/api/v1/milestones/{milestone.id}/cancel/",
        {
            "expected_version": milestone.version,
            "reason": "Client dropped the blog.",
            "impact": "Launch moves one week earlier; fee reduced by agreement.",
        },
        format="json",
    )
    assert done.status_code == 200, done.content
    assert done.json()["status"] == "cancelled"
    assert done.json()["cancellation_impact"].startswith("Launch moves")


def test_accepted_work_cannot_be_cancelled(workspace, project, delivery_manager):
    milestone = _milestone(workspace, project, status=MilestoneStatus.ACCEPTED)
    response = _api(delivery_manager).post(
        f"/api/v1/milestones/{milestone.id}/cancel/",
        {"expected_version": milestone.version, "reason": "x", "impact": "y"},
        format="json",
    )
    assert response.status_code == 409


def test_a_reviewer_sends_submitted_work_back_with_the_evidence_kept(
    workspace, project, delivery_manager, delivery_employee
):
    milestone = _milestone(
        workspace,
        project,
        status=MilestoneStatus.IN_REVIEW,
        evidence="Staging link: https://staging.example.test",
        submitted_at=timezone.now(),
        submitted_by=delivery_employee.user,
    )

    # The employee cannot send their own work back; that is the reviewer's act.
    assert (
        _api(delivery_employee)
        .post(
            f"/api/v1/milestones/{milestone.id}/send-back/",
            {"expected_version": milestone.version, "reason": "x"},
            format="json",
        )
        .status_code
        == 403
    )

    response = _api(delivery_manager).post(
        f"/api/v1/milestones/{milestone.id}/send-back/",
        {"expected_version": milestone.version, "reason": "Contact form does not send."},
        format="json",
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["status"] == "in_progress"
    assert body["submitted_at"] is None
    assert "Contact form does not send." in body["evidence"]
    assert "https://staging.example.test" in body["evidence"]


def test_a_task_can_belong_to_a_milestone_and_inherits_its_project(
    workspace, project, delivery_manager
):
    milestone = _milestone(workspace, project)
    api = _api(delivery_manager)
    response = api.post(
        "/api/v1/tasks/",
        {
            "title": "Collect brand assets",
            "due_at": (timezone.now() + timedelta(days=2)).isoformat(),
            "milestone": str(milestone.id),
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    assert response.json()["milestone"] == str(milestone.id)
    assert response.json()["project"] == str(project.id)

    listed = api.get("/api/v1/tasks/", {"milestone": str(milestone.id)}).json()["results"]
    assert [t["title"] for t in listed] == ["Collect brand assets"]


# ---------------------------------------------------------------------------
# CRM09
# ---------------------------------------------------------------------------


def test_a_ticket_records_its_next_action(owner):
    api = _api(owner)
    ticket = api.post(
        "/api/v1/tickets/",
        {"title": "Form broken", "origin": "client_request", "priority": "normal"},
        format="json",
    ).json()
    response = api.post(
        f"/api/v1/tickets/{ticket['id']}/next-action/",
        {"expected_version": ticket["version"], "next_action": "Check SMTP logs today."},
        format="json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["next_action"] == "Check SMTP logs today."

    stale = api.post(
        f"/api/v1/tickets/{ticket['id']}/next-action/",
        {"expected_version": ticket["version"], "next_action": "Overwrite"},
        format="json",
    )
    assert stale.status_code == 409


# ---------------------------------------------------------------------------
# CRM04
# ---------------------------------------------------------------------------


def test_an_activity_schedules_its_follow_up_in_one_step(workspace, sales_rep, contact_factory):
    with workspace_context(workspace.id):
        contact = contact_factory(workspace)
    api = _api(sales_rep)
    due = (timezone.now() + timedelta(days=1)).isoformat()

    response = api.post(
        "/api/v1/activities/",
        {
            "contact": str(contact.id),
            "kind": "call",
            "occurred_at": timezone.now().isoformat(),
            "outcome": "Interested; wants pricing.",
            "follow_up_title": "Send pricing",
            "follow_up_due_at": due,
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    task_id = response.json()["follow_up_task"]
    assert task_id is not None
    task = Task.objects.get(pk=task_id)
    assert task.title == "Send pricing"
    assert task.owner_id == sales_rep.user_id
    assert task.contact_id == contact.id


def test_a_follow_up_needs_both_title_and_due_time(workspace, sales_rep, contact_factory):
    with workspace_context(workspace.id):
        contact = contact_factory(workspace)
    response = _api(sales_rep).post(
        "/api/v1/activities/",
        {
            "contact": str(contact.id),
            "kind": "call",
            "occurred_at": timezone.now().isoformat(),
            "follow_up_title": "Send pricing",
        },
        format="json",
    )
    assert response.status_code == 400
    # Nothing half-written: the activity rolled back with the follow-up.
    assert not Activity.objects.filter(contact=contact).exists()


# ---------------------------------------------------------------------------
# CRM02
# ---------------------------------------------------------------------------


def test_merging_moves_history_and_keeps_the_duplicates_source(
    workspace, sales_manager, contact_factory
):
    with workspace_context(workspace.id):
        survivor = contact_factory(workspace, display_name="Amina Shah", source="referral")
        duplicate = contact_factory(
            workspace,
            display_name="amina  shah",
            source="event",
            source_reference="Web Summit stand",
            email="amina@example.test",
            normalized_email="amina@example.test",
        )
        lead = Lead.objects.create(workspace=workspace, contact=duplicate)

    api = _api(sales_manager)
    suggestions = api.get(f"/api/v1/contacts/{survivor.id}/duplicates/").json()
    assert [s["id"] for s in suggestions] == [str(duplicate.id)]
    assert suggestions[0]["is_definitive"] is False

    response = api.post(
        f"/api/v1/contacts/{survivor.id}/merge/",
        {
            "duplicate_id": str(duplicate.id),
            "survivor_version": survivor.version,
            "duplicate_version": duplicate.version,
            "reason": "Same person; met at Web Summit and later referred.",
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    # The survivor took the email it lacked.
    assert response.json()["email"] == "amina@example.test"

    lead.refresh_from_db()
    assert lead.contact_id == survivor.id

    retired = Contact.objects.get(pk=duplicate.id)
    assert retired.merged_into_id == survivor.id
    assert retired.deleted_at is not None
    assert retired.source_reference == "Web Summit stand"

    survivor.refresh_from_db()
    provenance = survivor.raw_import_values["merged_from"][0]
    assert provenance["source_reference"] == "Web Summit stand"


def test_a_representative_cannot_merge(workspace, sales_rep, contact_factory):
    with workspace_context(workspace.id):
        a = contact_factory(workspace)
        b = contact_factory(workspace)
    response = _api(sales_rep).post(
        f"/api/v1/contacts/{a.id}/merge/",
        {
            "duplicate_id": str(b.id),
            "survivor_version": a.version,
            "duplicate_version": b.version,
            "reason": "Same",
        },
        format="json",
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# CRM11: every figure exposes its underlying records
# ---------------------------------------------------------------------------


def test_each_dashboard_figure_reconciles_to_its_records(workspace, owner, contact_factory):
    with workspace_context(workspace.id):
        for _ in range(3):
            Lead.objects.create(workspace=workspace, contact=contact_factory(workspace))

    api = _api(owner)
    overview = api.get("/api/v1/reports/overview/").json()
    for measure, count in overview["attention"].items():
        drill = api.get("/api/v1/reports/records/", {"measure": measure})
        assert drill.status_code == 200, (measure, drill.content)
        assert drill.json()["count"] == count, measure

    unowned = api.get("/api/v1/reports/records/", {"measure": "leads_without_owner"}).json()
    assert unowned["count"] == 3
    assert {r["type"] for r in unowned["records"]} == {"lead"}

    pipeline = api.get("/api/v1/reports/records/", {"measure": "open_pipeline_value"}).json()
    assert pipeline["count"] == overview["open_pipeline_value"]["record_count"]


def test_an_unknown_measure_is_refused(owner):
    response = _api(owner).get("/api/v1/reports/records/", {"measure": "salaries"})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# CRM05/CRM06: the working calendar is configurable
# ---------------------------------------------------------------------------


def test_the_calendar_can_be_configured_and_bumps_its_version(owner, sales_rep):
    api = _api(owner)
    before = api.get("/api/v1/workspace/settings/").json()

    response = api.put(
        "/api/v1/workspace/settings/",
        {
            "time_zone": "Asia/Karachi",
            "working_weekdays": [0, 1, 2, 3, 4, 5],
            "working_intervals": [{"start": "10:00", "end": "19:00"}],
            "holidays": ["2026-12-25"],
            "default_currency": "pkr",
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["calendar_version"] == before["calendar_version"] + 1
    assert body["default_currency"] == "PKR"
    assert body["holidays"] == ["2026-12-25"]

    assert (
        _api(sales_rep).put("/api/v1/workspace/settings/", body, format="json").status_code == 403
    )


def test_overlapping_hours_and_bad_zones_are_refused(owner):
    response = _api(owner).put(
        "/api/v1/workspace/settings/",
        {
            "time_zone": "Mars/Olympus",
            "working_weekdays": [0],
            "working_intervals": [
                {"start": "09:00", "end": "13:00"},
                {"start": "12:00", "end": "17:00"},
            ],
            "holidays": [],
            "default_currency": "USD",
        },
        format="json",
    )
    assert response.status_code == 400
    errors = response.json()["field_errors"]
    assert "time_zone" in errors and "working_intervals" in errors


# ---------------------------------------------------------------------------
# CRM07: sales history remains accessible to authorised delivery staff
# ---------------------------------------------------------------------------


def test_delivery_sees_the_won_deal_behind_its_work_but_not_open_sales(
    workspace, sales_rep, delivery_manager, delivery_employee, contact_factory
):
    from modules.crm.models import Opportunity, OpportunityStage

    with workspace_context(workspace.id):
        contact = contact_factory(workspace, display_name="Acme Ltd")
        won = Opportunity.objects.create(
            workspace=workspace,
            contact=contact,
            service="SEO retainer",
            stage=OpportunityStage.WON,
            owner=sales_rep.user,
            accepted_scope_evidence="Signed proposal v2.",
            commercial_reference="PO-1",
        )
        Client.objects.create(
            workspace=workspace,
            contact=contact,
            display_name="Acme Ltd",
            status=ClientStatus.ACCEPTED,
            delivery_owner=delivery_employee.user,
            originating_opportunity=won,
        )
        still_open = Opportunity.objects.create(
            workspace=workspace,
            contact=contact,
            service="Paid ads",
            stage=OpportunityStage.PROPOSAL,
            owner=sales_rep.user,
        )

    for member in (delivery_manager, delivery_employee):
        api = _api(member)
        assert api.get(f"/api/v1/opportunities/{won.id}/").status_code == 200, member.role
        assert api.get(f"/api/v1/opportunities/{won.id}/stage-history/").status_code == 200
        assert api.get(f"/api/v1/opportunities/{still_open.id}/").status_code == 404

    # An employee who does not deliver this client does not see its deal.
    other = _make_member(workspace, "other-dev@scalevexo.test", Role.DELIVERY_EMPLOYEE)
    assert _api(other).get(f"/api/v1/opportunities/{won.id}/").status_code == 404
