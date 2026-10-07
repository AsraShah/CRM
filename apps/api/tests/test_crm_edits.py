"""CRM03 - editing leads, contacts and deals over PATCH.

Before these endpoints existed a deal's value could only be set when the deal
was created: one opened as "value not yet known" stayed unknown for ever, which
left the pipeline permanently undervalued.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from modules.crm.models import Opportunity

pytestmark = pytest.mark.django_db


@pytest.fixture
def manager_api(sales_manager) -> APIClient:
    api = APIClient()
    api.force_login(sales_manager.user)
    return api


@pytest.fixture
def rep_api(other_rep) -> APIClient:
    api = APIClient()
    api.force_login(other_rep.user)
    return api


def _contact(api, **extra) -> dict:
    response = api.post("/api/v1/contacts/", {"display_name": "Amina Shah", **extra}, format="json")
    assert response.status_code == 201, response.content
    return response.json()


def _deal(api) -> dict:
    contact = _contact(api, email="amina@example.test")
    response = api.post(
        "/api/v1/opportunities/",
        {"contact": contact["id"], "service": "SEO retainer"},
        format="json",
    )
    assert response.status_code == 201, response.content
    return response.json()


def test_an_unknown_deal_value_can_become_known(manager_api):
    deal = _deal(manager_api)
    assert deal["amount"] is None

    response = manager_api.patch(
        f"/api/v1/opportunities/{deal['id']}/",
        {"expected_version": deal["version"], "amount": "4500.00", "currency": "usd"},
        format="json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["amount"] == "4500.00"
    assert response.json()["currency"] == "USD"
    assert response.json()["version"] == deal["version"] + 1


def test_clearing_a_value_returns_it_to_unknown_not_zero(manager_api):
    deal = _deal(manager_api)
    priced = manager_api.patch(
        f"/api/v1/opportunities/{deal['id']}/",
        {"expected_version": deal["version"], "amount": "100", "currency": "USD"},
        format="json",
    ).json()
    cleared = manager_api.patch(
        f"/api/v1/opportunities/{deal['id']}/",
        {"expected_version": priced["version"], "amount": None},
        format="json",
    )
    assert cleared.status_code == 200
    assert cleared.json()["amount"] is None
    assert cleared.json()["currency"] == ""


def test_an_amount_without_a_currency_is_refused(manager_api):
    deal = _deal(manager_api)
    response = manager_api.patch(
        f"/api/v1/opportunities/{deal['id']}/",
        {"expected_version": deal["version"], "amount": "100"},
        format="json",
    )
    assert response.status_code == 400
    assert "currency" in response.json()["field_errors"]


def test_a_stale_edit_does_not_overwrite_a_colleagues_change(manager_api):
    deal = _deal(manager_api)
    first = manager_api.patch(
        f"/api/v1/opportunities/{deal['id']}/",
        {"expected_version": deal["version"], "service": "SEO retainer, 6 months"},
        format="json",
    )
    assert first.status_code == 200

    stale = manager_api.patch(
        f"/api/v1/opportunities/{deal['id']}/",
        {"expected_version": deal["version"], "service": "Something else"},
        format="json",
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "version_conflict"
    assert Opportunity.objects.get(pk=deal["id"]).service == "SEO retainer, 6 months"


def test_stage_cannot_be_changed_by_an_edit(manager_api):
    """Stage moves only through transition or convert, which carry evidence."""
    deal = _deal(manager_api)
    response = manager_api.patch(
        f"/api/v1/opportunities/{deal['id']}/",
        {"expected_version": deal["version"], "stage": "won", "service": "Retainer"},
        format="json",
    )
    assert response.status_code == 200
    assert Opportunity.objects.get(pk=deal["id"]).stage == "discovery"


def test_a_closed_deal_is_read_only(manager_api):
    deal = _deal(manager_api)
    lost = manager_api.post(
        f"/api/v1/opportunities/{deal['id']}/transition/",
        {
            "target_stage": "lost",
            "expected_version": deal["version"],
            "lost_reason": "Chose an in-house hire.",
        },
        format="json",
    )
    assert lost.status_code == 200, lost.content

    response = manager_api.patch(
        f"/api/v1/opportunities/{deal['id']}/",
        {"expected_version": lost.json()["version"], "amount": "1", "currency": "USD"},
        format="json",
    )
    assert response.status_code == 409


def test_a_rep_cannot_edit_a_colleagues_deal(manager_api, rep_api):
    deal = _deal(manager_api)
    response = rep_api.patch(
        f"/api/v1/opportunities/{deal['id']}/",
        {"expected_version": deal["version"], "service": "Hijacked"},
        format="json",
    )
    assert response.status_code in {403, 404}
    assert Opportunity.objects.get(pk=deal["id"]).service == "SEO retainer"


def test_contact_edit_renormalises_and_refuses_a_duplicate(manager_api):
    first = _contact(manager_api, email="one@example.test")
    second = _contact(manager_api, email="two@example.test")

    renamed = manager_api.patch(
        f"/api/v1/contacts/{first['id']}/",
        {"expected_version": first["version"], "email": "One.New@Example.TEST"},
        format="json",
    )
    assert renamed.status_code == 200, renamed.content

    clash = manager_api.patch(
        f"/api/v1/contacts/{second['id']}/",
        {"expected_version": second["version"], "email": "one.new@example.test"},
        format="json",
    )
    assert clash.status_code == 409
    assert clash.json()["code"] == "duplicate_contact"


def test_lead_edit_keeps_need_and_fit_once_qualified(manager_api, sales_manager):
    contact = _contact(manager_api, email="lead@example.test")
    lead = manager_api.post(
        "/api/v1/leads/",
        {"contact": contact["id"], "owner": str(sales_manager.user_id)},
        format="json",
    ).json()
    qualified = manager_api.post(
        f"/api/v1/leads/{lead['id']}/status/",
        {
            "status": "qualified",
            "expected_version": lead["version"],
            "need": "Inbound has stalled.",
            "fit": "Matches our retainer.",
        },
        format="json",
    )
    assert qualified.status_code == 200, qualified.content
    assert qualified.json()["need"] == "Inbound has stalled."

    cleared = manager_api.patch(
        f"/api/v1/leads/{lead['id']}/",
        {"expected_version": qualified.json()["version"], "fit": ""},
        format="json",
    )
    assert cleared.status_code == 400
    assert "fit" in cleared.json()["field_errors"]

    noted = manager_api.patch(
        f"/api/v1/leads/{lead['id']}/",
        {"expected_version": qualified.json()["version"], "notes": "Prefers calls."},
        format="json",
    )
    assert noted.status_code == 200
    assert noted.json()["notes"] == "Prefers calls."
