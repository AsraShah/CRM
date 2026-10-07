"""Input limits and typed fields (modules/common/limits.py).

Every writable text field has a ceiling, required text may not be blank or
whitespace, and structured values (currency and country codes, phone numbers,
money, dates, ids) are checked at the edge with a message saying what is
expected. The interface mirrors these limits in apps/web/src/lib/limits.ts.
"""

from __future__ import annotations

import uuid

import pytest
from rest_framework.test import APIClient

from modules.common import limits

pytestmark = pytest.mark.django_db


@pytest.fixture
def api(sales_manager) -> APIClient:
    client = APIClient()
    client.force_login(sales_manager.user)
    return client


def _contact(api, **extra) -> dict:
    response = api.post("/api/v1/contacts/", {"display_name": "Amina Shah", **extra}, format="json")
    assert response.status_code == 201, response.content
    return response.json()


def _lead(api) -> dict:
    contact = _contact(api)
    response = api.post("/api/v1/leads/", {"contact": contact["id"]}, format="json")
    assert response.status_code == 201, response.content
    return response.json()


# ---------------------------------------------------------------------------
# Length ceilings on text that used to be unbounded
# ---------------------------------------------------------------------------


def test_lead_notes_are_capped(api):
    contact = _contact(api)
    too_long = "x" * (limits.LONG_TEXT + 1)
    response = api.post(
        "/api/v1/leads/", {"contact": contact["id"], "notes": too_long}, format="json"
    )
    assert response.status_code == 400
    assert "notes" in response.json()["field_errors"]

    at_limit = "x" * limits.LONG_TEXT
    response = api.post(
        "/api/v1/leads/", {"contact": contact["id"], "notes": at_limit}, format="json"
    )
    assert response.status_code == 201, response.content


def test_qualification_need_and_fit_are_capped(api):
    lead = _lead(api)
    response = api.patch(
        f"/api/v1/leads/{lead['id']}/",
        {"expected_version": lead["version"], "need": "n" * (limits.REASON + 1)},
        format="json",
    )
    assert response.status_code == 400
    assert "need" in response.json()["field_errors"]


def test_ticket_description_is_capped(api):
    response = api.post(
        "/api/v1/tickets/",
        {"title": "Form broken", "description": "d" * (limits.LONG_TEXT + 1)},
        format="json",
    )
    assert response.status_code == 400
    assert "description" in response.json()["field_errors"]


def test_task_description_is_capped(api, sales_manager):
    response = api.post(
        "/api/v1/tasks/",
        {
            "title": "Call back",
            "due_at": "2030-01-01T10:00:00Z",
            "description": "d" * (limits.LONG_TEXT + 1),
        },
        format="json",
    )
    assert response.status_code == 400
    assert "description" in response.json()["field_errors"]


def test_required_text_cannot_be_only_whitespace(api):
    response = api.post("/api/v1/contacts/", {"display_name": "    "}, format="json")
    assert response.status_code == 400
    assert "display_name" in response.json()["field_errors"]


# ---------------------------------------------------------------------------
# Typed values
# ---------------------------------------------------------------------------


def test_contact_email_must_be_an_email_address(api):
    response = api.post(
        "/api/v1/contacts/",
        {"display_name": "Amina Shah", "email": "not-an-email"},
        format="json",
    )
    assert response.status_code == 400
    assert "email" in response.json()["field_errors"]


@pytest.mark.parametrize("value", ["+92 300 1234567", "(021) 555-0101", "0300-1234567 ext 12"])
def test_ordinary_phone_numbers_are_accepted(api, value):
    response = api.post(
        "/api/v1/contacts/",
        {"display_name": "Amina Shah", "phone": value, "default_country": "pk"},
        format="json",
    )
    assert response.status_code == 201, response.content


@pytest.mark.parametrize("value", ["call me", "amina@example.test", "12"])
def test_text_in_a_phone_field_is_refused(api, value):
    response = api.post(
        "/api/v1/contacts/", {"display_name": "Amina Shah", "phone": value}, format="json"
    )
    assert response.status_code == 400
    assert "phone" in response.json()["field_errors"]


def test_country_code_must_be_two_letters(api):
    response = api.post(
        "/api/v1/contacts/",
        {"display_name": "Amina Shah", "phone": "0300 1234567", "default_country": "P1"},
        format="json",
    )
    assert response.status_code == 400
    assert "default_country" in response.json()["field_errors"]


def test_currency_is_upper_cased_and_must_be_three_letters(api):
    contact = _contact(api)
    created = api.post(
        "/api/v1/opportunities/",
        {"contact": contact["id"], "service": "SEO", "amount": "100", "currency": "eur"},
        format="json",
    )
    assert created.status_code == 201, created.content
    assert created.json()["currency"] == "EUR"

    refused = api.post(
        "/api/v1/opportunities/",
        {"contact": contact["id"], "service": "SEO", "amount": "100", "currency": "EU1"},
        format="json",
    )
    assert refused.status_code == 400
    assert "currency" in refused.json()["field_errors"]


def test_a_deal_value_cannot_be_negative(api):
    contact = _contact(api)
    response = api.post(
        "/api/v1/opportunities/",
        {"contact": contact["id"], "service": "SEO", "amount": "-5", "currency": "USD"},
        format="json",
    )
    assert response.status_code == 400
    assert "amount" in response.json()["field_errors"]


def test_a_deal_value_must_be_a_number(api):
    contact = _contact(api)
    response = api.post(
        "/api/v1/opportunities/",
        {"contact": contact["id"], "service": "SEO", "amount": "lots", "currency": "USD"},
        format="json",
    )
    assert response.status_code == 400
    assert "amount" in response.json()["field_errors"]


def test_import_mapping_is_bounded(api):
    from modules.crm.serializers import ImportPreviewRequestSerializer

    too_many = {f"Column {i}": "" for i in range(201)}
    serializer = ImportPreviewRequestSerializer(data={"column_mapping": too_many})
    assert not serializer.is_valid()
    assert "column_mapping" in serializer.errors


def test_promised_end_cannot_precede_start():
    from modules.work.delivery_serializers import ConvertOpportunitySerializer

    serializer = ConvertOpportunitySerializer(
        data={
            "expected_version": 1,
            "accepted_scope_evidence": "Signed proposal v2.",
            "commercial_reference": "PO-1",
            "delivery_owner_id": str(uuid.uuid4()),
            "promised_start_on": "2030-02-01",
            "promised_end_on": "2030-01-01",
        }
    )
    assert not serializer.is_valid()
    assert "promised_end_on" in serializer.errors


def test_a_dependency_must_name_a_milestone_id(api):
    response = api.post(
        f"/api/v1/milestones/{uuid.uuid4()}/dependencies/",
        {"depends_on": "not-an-id"},
        format="json",
    )
    assert response.status_code in {400, 403}
    if response.status_code == 400:
        assert "depends_on" in response.json()["field_errors"]


def test_the_ai_switch_reads_false_as_false():
    """The string "false" used to be coerced with bool(), which is True."""
    from modules.ai.views import KillSwitchSerializer

    serializer = KillSwitchSerializer(data={"enabled": "false"})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["enabled"] is False

    refused = KillSwitchSerializer(data={"enabled": "maybe"})
    assert not refused.is_valid()
