"""HTTP-layer tests for the Stage 2 endpoints.

`test_api.py` covers the sales half. These cover delivery, support, exception
review and the receipt register — the endpoints a delivery manager and the CEO
actually use, and the ones where a permission mistake is least visible because
fewer people exercise them.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from modules.common.tenancy import workspace_context
from modules.crm import conversion
from modules.crm import services as crm_services
from modules.crm.models import OpportunityStage
from modules.identity.models import Role

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def delivery_manager(workspace):
    from tests.conftest import _make_member

    return _make_member(workspace, "delivery-api@scalevexo.test", Role.DELIVERY_MANAGER)


@pytest.fixture
def delivery_employee(workspace):
    from tests.conftest import _make_member

    return _make_member(workspace, "dev-api@scalevexo.test", Role.DELIVERY_EMPLOYEE)


def _client_for(membership) -> APIClient:
    api = APIClient()
    api.force_login(membership.user)
    return api


@pytest.fixture
def converted(workspace, owner, delivery_manager, contact_factory):
    """A won deal with its client, project and milestones."""
    with workspace_context(workspace.id):
        contact = contact_factory(workspace, display_name="Delivered Ltd")
        deal = crm_services.create_opportunity(
            membership=owner,
            actor=owner.user,
            contact=contact,
            service="Rebuild",
            amount=Decimal("8000.00"),
            currency="USD",
        )
        for target, extra in (
            (OpportunityStage.QUALIFIED, {}),
            (OpportunityStage.PROPOSAL, {"scope_reference": "SOW-API"}),
            (OpportunityStage.NEGOTIATION, {}),
        ):
            deal = crm_services.transition_opportunity(
                membership=owner,
                actor=owner.user,
                opportunity_id=deal.id,
                target_stage=target,
                expected_version=deal.version,
                **extra,
            )
        yield conversion.convert_won_deal(
            membership=owner,
            actor=owner.user,
            opportunity_id=deal.id,
            expected_version=deal.version,
            accepted_scope_evidence="Signed SOW-API.",
            commercial_reference="INV-API-1",
            delivery_owner_id=delivery_manager.user_id,
        )


# ---------------------------------------------------------------------------
# Clients and handover
# ---------------------------------------------------------------------------


def test_client_list_shows_what_sales_promised(delivery_manager, converted):
    """Delivery sees the commitments without asking for another spreadsheet."""
    api = _client_for(delivery_manager)
    results = api.get("/api/v1/clients/").json()["results"]

    assert len(results) == 1
    record = results[0]
    assert record["accepted_scope"] == "Signed SOW-API."
    assert record["commercial_reference"] == "INV-API-1"
    assert record["awaiting_handover"] is True


def test_handover_accept_and_return_are_separate_endpoints(delivery_manager, converted):
    api = _client_for(delivery_manager)
    client_id = converted.client.id

    returned = api.post(
        f"/api/v1/clients/{client_id}/return-handover/",
        {
            "expected_version": converted.client.version,
            "missing_information": "No access credentials were supplied.",
        },
        format="json",
    )
    assert returned.status_code == 200
    assert "credentials" in returned.json()["handover_returned_reason"]
    # Returning is not rejecting: it stays pending until sales fix it.
    assert returned.json()["status"] == "pending_handover"

    accepted = api.post(
        f"/api/v1/clients/{client_id}/accept-handover/",
        {"expected_version": returned.json()["version"], "note": "Received now."},
        format="json",
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"


def test_returning_a_handover_without_specifics_is_refused(delivery_manager, converted):
    api = _client_for(delivery_manager)
    response = api.post(
        f"/api/v1/clients/{converted.client.id}/return-handover/",
        {"expected_version": converted.client.version, "missing_information": ""},
        format="json",
    )
    assert response.status_code == 400


def test_a_sales_rep_cannot_accept_a_handover(sales_rep, converted):
    api = _client_for(sales_rep)
    response = api.post(
        f"/api/v1/clients/{converted.client.id}/accept-handover/",
        {"expected_version": converted.client.version},
        format="json",
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Projects and milestones
# ---------------------------------------------------------------------------


def test_project_detail_embeds_its_milestones(delivery_manager, converted):
    api = _client_for(delivery_manager)
    project = api.get(f"/api/v1/projects/{converted.project.id}/").json()

    assert len(project["milestones"]) == 4
    assert project["milestones"][0]["status"] == "planned"
    assert project["milestones"][0]["unmet_dependencies"] == []


def test_milestone_lifecycle_over_http(delivery_manager, delivery_employee, converted):
    employee = _client_for(delivery_employee)
    manager = _client_for(delivery_manager)
    milestone = converted.project.milestones.order_by("sequence").first()

    started = employee.post(
        f"/api/v1/milestones/{milestone.id}/start/",
        {"expected_version": milestone.version},
        format="json",
    )
    assert started.status_code == 200
    assert started.json()["status"] == "in_progress"

    submitted = employee.post(
        f"/api/v1/milestones/{milestone.id}/submit/",
        {"evidence": "Booked for Tuesday.", "expected_version": started.json()["version"]},
        format="json",
    )
    assert submitted.json()["status"] == "in_review"

    # The employee who submitted cannot also accept.
    refused = employee.post(
        f"/api/v1/milestones/{milestone.id}/accept/",
        {"expected_version": submitted.json()["version"]},
        format="json",
    )
    assert refused.status_code == 403

    accepted = manager.post(
        f"/api/v1/milestones/{milestone.id}/accept/",
        {"expected_version": submitted.json()["version"], "note": "Verified."},
        format="json",
    )
    assert accepted.json()["status"] == "accepted"


def test_submitting_without_evidence_is_refused(delivery_employee, converted):
    api = _client_for(delivery_employee)
    milestone = converted.project.milestones.order_by("sequence").first()

    api.post(
        f"/api/v1/milestones/{milestone.id}/start/",
        {"expected_version": milestone.version},
        format="json",
    )
    milestone.refresh_from_db()

    response = api.post(
        f"/api/v1/milestones/{milestone.id}/submit/",
        {"evidence": "   ", "expected_version": milestone.version},
        format="json",
    )
    assert response.status_code == 400


def test_dependencies_block_acceptance_over_http(delivery_manager, delivery_employee, converted):
    manager = _client_for(delivery_manager)
    employee = _client_for(delivery_employee)
    first, second = list(converted.project.milestones.order_by("sequence")[:2])

    linked = manager.post(
        f"/api/v1/milestones/{second.id}/dependencies/",
        {"depends_on": str(first.id)},
        format="json",
    )
    assert linked.status_code == 201
    assert len(linked.json()["unmet_dependencies"]) == 1

    second.refresh_from_db()
    employee.post(
        f"/api/v1/milestones/{second.id}/start/",
        {"expected_version": second.version},
        format="json",
    )
    second.refresh_from_db()
    submitted = employee.post(
        f"/api/v1/milestones/{second.id}/submit/",
        {"evidence": "Done out of order.", "expected_version": second.version},
        format="json",
    )

    blocked = manager.post(
        f"/api/v1/milestones/{second.id}/accept/",
        {"expected_version": submitted.json()["version"]},
        format="json",
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "unmet_dependencies"

    overridden = manager.post(
        f"/api/v1/milestones/{second.id}/accept/",
        {
            "expected_version": submitted.json()["version"],
            "override_dependencies": True,
            "override_reason": "Client signed off the design verbally.",
        },
        format="json",
    )
    assert overridden.json()["status"] == "accepted"
    assert "verbally" in overridden.json()["dependency_override_reason"]


def test_scope_changes_are_listed_and_recorded(delivery_manager, converted):
    api = _client_for(delivery_manager)
    url = f"/api/v1/projects/{converted.project.id}/scope-changes/"

    assert api.get(url).json() == []

    created = api.post(
        url,
        {
            "kind": "scope_change",
            "description": "Second language added.",
            "commercial_impact": "15 further hours, quoted separately.",
        },
        format="json",
    )
    assert created.status_code == 201
    assert created.json()["kind"] == "scope_change"
    assert len(api.get(url).json()) == 1


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------


@pytest.fixture
def ticket(delivery_employee, converted):
    api = _client_for(delivery_employee)
    return api.post(
        "/api/v1/tickets/",
        {
            "title": "Form submission fails",
            "client": str(converted.client.id),
            "priority": "high",
        },
        format="json",
    ).json()


def test_ticket_waiting_state_needs_an_owner_and_a_review_time(delivery_employee, ticket):
    api = _client_for(delivery_employee)
    version = ticket["version"]

    triaged = api.post(
        f"/api/v1/tickets/{ticket['id']}/transition/",
        {"target_state": "triaged", "expected_version": version},
        format="json",
    )

    incomplete = api.post(
        f"/api/v1/tickets/{ticket['id']}/transition/",
        {
            "target_state": "waiting_customer",
            "expected_version": triaged.json()["version"],
            "waiting_reason": "Awaiting their confirmation.",
        },
        format="json",
    )
    assert incomplete.status_code == 400

    complete = api.post(
        f"/api/v1/tickets/{ticket['id']}/transition/",
        {
            "target_state": "waiting_customer",
            "expected_version": triaged.json()["version"],
            "waiting_reason": "Awaiting their confirmation.",
            "waiting_next_owner_id": str(delivery_employee.user_id),
            "waiting_review_at": (timezone.now() + timedelta(days=2)).isoformat(),
        },
        format="json",
    )
    assert complete.status_code == 200
    assert complete.json()["state"] == "waiting_customer"


def test_ticket_state_history_records_the_supersession(delivery_employee, delivery_manager, ticket):
    api = _client_for(delivery_employee)
    version = ticket["version"]

    for target, extra in (("triaged", {}), ("in_progress", {})):
        resp = api.post(
            f"/api/v1/tickets/{ticket['id']}/transition/",
            {"target_state": target, "expected_version": version, **extra},
            format="json",
        )
        version = resp.json()["version"]

    resolved = api.post(
        f"/api/v1/tickets/{ticket['id']}/transition/",
        {
            "target_state": "resolved",
            "expected_version": version,
            "resolution_note": "Corrected the SMTP credentials.",
            "closure_test_result": "Submitted on Chrome and Safari.",
        },
        format="json",
    )

    reopened = api.post(
        f"/api/v1/tickets/{ticket['id']}/transition/",
        {
            "target_state": "triaged",
            "expected_version": resolved.json()["version"],
            "reason": "Still failing on mobile.",
        },
        format="json",
    )
    assert reopened.json()["reopen_count"] == 1

    history = api.get(f"/api/v1/tickets/{ticket['id']}/state-history/").json()
    assert "SMTP credentials" in history[-1]["superseded_resolution"]


def test_only_a_manager_can_publish_a_comment_to_the_client(
    delivery_employee, delivery_manager, ticket
):
    employee = _client_for(delivery_employee)
    manager = _client_for(delivery_manager)
    url = f"/api/v1/tickets/{ticket['id']}/comments/"

    internal = employee.post(url, {"body": "Their DNS is wrong."}, format="json")
    assert internal.status_code == 201
    assert internal.json()["visibility"] == "internal"

    refused = employee.post(
        url,
        {"body": "Fixed by Friday.", "visibility": "client_visible"},
        format="json",
    )
    assert refused.status_code == 403

    published = manager.post(
        url,
        {"body": "Fixed by Friday.", "visibility": "client_visible"},
        format="json",
    )
    assert published.json()["visibility"] == "client_visible"
    assert len(employee.get(url).json()) == 2


# ---------------------------------------------------------------------------
# Exceptions and receipts
# ---------------------------------------------------------------------------


def test_exception_queue_and_review_over_http(workspace, owner, sales_rep):
    from modules.reporting import exceptions_service
    from modules.work.models import Task

    with workspace_context(workspace.id):
        Task.objects.create(
            workspace=workspace,
            title="Overdue call",
            owner=sales_rep.user,
            status="open",
            due_at=timezone.now() - timedelta(days=2),
            original_due_at=timezone.now() - timedelta(days=2),
        )
        exceptions_service.sweep()

    employee = _client_for(sales_rep)
    mine = employee.get("/api/v1/exceptions/").json()["results"]
    assert len(mine) == 1

    explained = employee.post(
        f"/api/v1/exceptions/{mine[0]['id']}/explain/",
        {"explanation": "The client cancelled.", "dispute": True},
        format="json",
    )
    assert explained.json()["state"] == "disputed"

    # The employee cannot close their own exception.
    assert (
        employee.post(
            f"/api/v1/exceptions/{mine[0]['id']}/review/",
            {"decision": "Closing it myself."},
            format="json",
        ).status_code
        == 403
    )

    manager = _client_for(owner)
    reviewed = manager.post(
        f"/api/v1/exceptions/{mine[0]['id']}/review/",
        {"decision": "Agreed, no action needed."},
        format="json",
    )
    assert reviewed.json()["state"] == "resolved"


def test_receipts_are_owner_only_and_need_evidence(workspace, owner, sales_manager, converted):
    manager = _client_for(sales_manager)
    assert manager.get("/api/v1/receipts/").status_code == 403

    api = _client_for(owner)
    payload = {
        "client": str(converted.client.id),
        "amount": "4000.00",
        "currency": "usd",
        "received_at": date.today().isoformat(),
        "evidence_reference": "Bank ref 88213",
    }
    created = api.post("/api/v1/receipts/", payload, format="json")
    assert created.status_code == 201
    assert created.json()["currency"] == "USD"

    # Now it shows as cash received, separately from won value.
    overview = api.get("/api/v1/reports/overview/").json()
    assert overview["cash_received"]["amounts"]["USD"] == "4000.00"
    assert overview["won_contract_value"]["amounts"]["USD"] == "8000.00"


def test_operational_reports_are_management_only(sales_rep, owner):
    assert _client_for(sales_rep).get("/api/v1/reports/operational/").status_code == 403

    body = _client_for(owner).get("/api/v1/reports/operational/").json()
    assert set(body) == {
        "conversion_by_source",
        "stage_aging",
        "activity_evidence",
        "delivery_health",
        "pilot_measures",
    }


def test_ai_budget_endpoint_reports_the_position(owner):
    body = _client_for(owner).get("/api/v1/ai/budget/").json()
    assert body["enabled"] is False  # ships disabled
    assert "remaining_usd" in body
    assert body["uncertain_reservations"] == 0
