"""CRM06 / TEST07 - rule administration and alert handling over HTTP.

The engine existed, but nothing let an administrator see, edit, pause or
simulate a rule, and alerts could not be acknowledged or resolved. These tests
cover the endpoints that close that gap.
"""

from __future__ import annotations

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from modules.automation import engine
from modules.automation.engine import JobOutcome
from modules.automation.models import (
    Job,
    Notification,
    NotificationState,
    OutboxEvent,
    RuleDefinition,
    RuleTemplate,
)
from modules.common.tenancy import workspace_context

pytestmark = pytest.mark.django_db


def _client(membership) -> APIClient:
    api = APIClient()
    api.force_login(membership.user)
    return api


@pytest.fixture
def admin_api(owner) -> APIClient:
    # The owner fixture has MFA enrolled, which rule.manage requires.
    return _client(owner)


@pytest.fixture
def installed(admin_api) -> dict[str, dict]:
    response = admin_api.post("/api/v1/rules/install-catalogue/")
    assert response.status_code == 200, response.content
    rules = admin_api.get("/api/v1/rules/").json()
    return {rule["template"]: rule for rule in rules}


def test_only_an_administrator_with_mfa_can_see_rules(sales_rep, owner):
    assert _client(sales_rep).get("/api/v1/rules/").status_code == 403

    owner.mfa_enrolled = False
    owner.save(update_fields=["mfa_enrolled"])
    assert _client(owner).get("/api/v1/rules/").status_code == 403


def test_the_catalogue_installs_once_and_disabled(admin_api, installed):
    # A01-A07. A08 (repeated rescheduling) is raised by the exception sweep,
    # because its outcome is a reviewable exception, not a rule action.
    assert len(installed) == 7
    assert all(rule["enabled"] is False for rule in installed.values())
    again = admin_api.post("/api/v1/rules/install-catalogue/")
    assert again.json() == {"installed": 0}


def test_editing_writes_a_new_version_and_cancels_work_queued_under_the_old(
    admin_api, installed, workspace, contact_factory
):
    from modules.crm.models import Lead

    rule = installed[RuleTemplate.UNASSIGNED_LEAD]
    with workspace_context(workspace.id):
        lead = Lead.objects.create(workspace=workspace, contact=contact_factory(workspace))
        old = RuleDefinition.objects.get(pk=rule["id"])
        old.enabled = True
        old.save(update_fields=["enabled"])
        event = OutboxEvent.objects.create(
            workspace=workspace,
            event_type="lead.created",
            aggregate_type="lead",
            aggregate_id=lead.id,
        )
        queued = Job.objects.create(
            workspace=workspace,
            job_type="rule:notify_manager",
            dedupe_key=f"lead:{lead.id}|{event.id}|a01@1|0",
            source_event=event,
            rule=old,
            rule_version=old.rule_version,
            payload={"action_index": 0, "aggregate_type": "lead", "aggregate_id": str(lead.id)},
            due_at=timezone.now(),
        )

    response = admin_api.patch(
        f"/api/v1/rules/{rule['id']}/",
        {
            "expected_version": old.version,
            "delay": {"working_minutes": 60},
            "explanation": "Unowned for an hour: nobody is responsible yet.",
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    successor = response.json()
    assert successor["rule_version"] == 2
    assert successor["delay"] == {"working_minutes": 60}
    assert successor["enabled"] is True
    assert successor["id"] != rule["id"]

    # Only the new version is listed.
    listed = {r["template"]: r for r in admin_api.get("/api/v1/rules/").json()}
    assert listed[RuleTemplate.UNASSIGNED_LEAD]["id"] == successor["id"]

    with workspace_context(workspace.id):
        queued.refresh_from_db()
        outcome, _ = engine.execute_job(queued)
    assert outcome == JobOutcome.CANCELLED


def test_a_rule_outside_the_closed_schema_is_refused(admin_api, installed):
    rule = installed[RuleTemplate.CRITICAL_TICKET]
    response = admin_api.patch(
        f"/api/v1/rules/{rule['id']}/",
        {
            "expected_version": rule["version"],
            "actions": [{"type": "notify_owner", "channel": "email"}],
        },
        format="json",
    )
    assert response.status_code == 400
    assert "actions" in response.json()["field_errors"]


def test_the_trigger_cannot_be_changed(admin_api, installed):
    rule = installed[RuleTemplate.CRITICAL_TICKET]
    response = admin_api.patch(
        f"/api/v1/rules/{rule['id']}/",
        {"expected_version": rule["version"], "trigger": "lead.created"},
        format="json",
    )
    # Trigger is not an editable field, so this is an edit with nothing in it:
    # refused, and no new version is written.
    assert response.status_code == 400
    assert RuleDefinition.objects.get(pk=rule["id"]).trigger == "ticket.created"
    assert RuleDefinition.objects.filter(template=RuleTemplate.CRITICAL_TICKET).count() == 1


def test_pausing_stops_the_rule_and_resuming_restores_it(admin_api, installed):
    rule = installed[RuleTemplate.OVERDUE_FOLLOWUP]
    enabled = admin_api.post(
        f"/api/v1/rules/{rule['id']}/enable/", {"expected_version": rule["version"]}, format="json"
    ).json()
    assert enabled["enabled"] is True

    paused = admin_api.post(
        f"/api/v1/rules/{rule['id']}/pause/",
        {"expected_version": enabled["version"]},
        format="json",
    ).json()
    assert paused["paused"] is True
    assert RuleDefinition.objects.get(pk=rule["id"]).is_runnable is False

    stale = admin_api.post(
        f"/api/v1/rules/{rule['id']}/resume/",
        {"expected_version": enabled["version"]},
        format="json",
    )
    assert stale.status_code == 409

    resumed = admin_api.post(
        f"/api/v1/rules/{rule['id']}/resume/",
        {"expected_version": paused["version"]},
        format="json",
    ).json()
    assert resumed["paused"] is False
    assert RuleDefinition.objects.get(pk=rule["id"]).is_runnable is True


def test_simulation_reports_matches_and_writes_nothing(
    admin_api, installed, workspace, contact_factory
):
    from modules.crm.models import Lead

    with workspace_context(workspace.id):
        lead = Lead.objects.create(workspace=workspace, contact=contact_factory(workspace))
    rule = installed[RuleTemplate.UNASSIGNED_LEAD]
    before = (Notification.objects.count(), Job.objects.count())

    response = admin_api.post(
        f"/api/v1/rules/{rule['id']}/simulate/",
        {"entity_type": "lead", "entity_id": str(lead.id)},
        format="json",
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["would_fire"] is True
    assert body["conditions"][0]["matched"] is True
    assert body["proposed_actions"] == ["notify_manager"]
    assert (Notification.objects.count(), Job.objects.count()) == before


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


@pytest.fixture
def alert(workspace, sales_rep):
    with workspace_context(workspace.id):
        return Notification.objects.create(
            workspace=workspace,
            recipient=sales_rep.user,
            dedupe_key="lead:x|unassigned_lead@1",
            title="Lead is still unassigned",
            cause="A new lead has had no owner for 30 working minutes.",
            state=NotificationState.DELIVERED,
        )


def test_the_recipient_acknowledges_and_a_manager_resolves(alert, sales_rep, sales_manager):
    rep = _client(sales_rep)
    acknowledged = rep.post(f"/api/v1/alerts/{alert.id}/acknowledge/")
    assert acknowledged.status_code == 200
    assert acknowledged.json()["state"] == "acknowledged"

    manager = _client(sales_manager)
    missing_note = manager.post(f"/api/v1/alerts/{alert.id}/resolve/", {"note": " "}, format="json")
    assert missing_note.status_code == 400

    resolved = manager.post(
        f"/api/v1/alerts/{alert.id}/resolve/", {"note": "Assigned to Sam."}, format="json"
    )
    assert resolved.status_code == 200
    assert resolved.json()["state"] == "resolved"
    assert resolved.json()["resolution_note"] == "Assigned to Sam."


def test_a_colleague_cannot_see_or_handle_someone_elses_alert(alert, other_rep):
    colleague = _client(other_rep)
    assert colleague.get(f"/api/v1/alerts/{alert.id}/").status_code == 404
    assert colleague.post(f"/api/v1/alerts/{alert.id}/acknowledge/").status_code == 404
    assert alert.id not in [a["id"] for a in colleague.get("/api/v1/alerts/").json()["results"]]
