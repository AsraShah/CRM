"""Rate limits, upload ceilings and silently dropped references.

Each test pins a defect found in review: a throttle scope that covered reads as
well as the expensive action, a size check that ran only after the whole upload
was in memory, and lookups that quietly discarded an unknown id instead of
refusing the request.
"""

from __future__ import annotations

import uuid
from datetime import date
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _private_files(settings, tmp_path):
    settings.PRIVATE_FILE_ROOT = tmp_path


def _client_for(membership) -> APIClient:
    client = APIClient()
    client.force_login(membership.user)
    return client


def _csv(name: str = "contacts.csv") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"name,email\nAmina,amina@example.test\n", "text/csv")


def test_listing_imports_does_not_spend_the_upload_allowance(sales_manager):
    api = _client_for(sales_manager)
    for _ in range(15):
        assert api.get("/api/v1/imports/").status_code == 200

    # The allowance is untouched, so an upload still goes through.
    assert api.post("/api/v1/imports/", {"file": _csv()}, format="multipart").status_code == 201


def test_uploads_are_still_limited(sales_manager):
    api = _client_for(sales_manager)
    statuses = [
        api.post("/api/v1/imports/", {"file": _csv()}, format="multipart").status_code
        for _ in range(11)
    ]
    assert statuses[:10] == [201] * 10
    assert statuses[10] == 429


def test_listing_ai_drafts_does_not_spend_the_generation_allowance(sales_rep):
    api = _client_for(sales_rep)
    for _ in range(25):
        assert api.get("/api/v1/ai/drafts/").status_code == 200


def test_oversized_upload_is_refused_before_it_is_read(sales_manager, settings):
    settings.IMPORT_MAX_BYTES = 10
    api = _client_for(sales_manager)
    with mock.patch("modules.crm.views.import_service.store_upload") as store:
        response = api.post("/api/v1/imports/", {"file": _csv()}, format="multipart")
    assert response.status_code == 413
    store.assert_not_called()


def test_ai_kill_switch_is_administrator_only(sales_rep, owner):
    payload = {"enabled": False, "reason": "Pausing spend."}
    assert _client_for(sales_rep).get("/api/v1/ai/budget/").status_code == 200
    assert (
        _client_for(sales_rep).post("/api/v1/ai/budget/", payload, format="json").status_code == 403
    )
    assert _client_for(owner).post("/api/v1/ai/budget/", payload, format="json").status_code == 200


def test_invitation_to_an_unknown_team_is_refused(owner):
    response = _client_for(owner).post(
        "/api/v1/memberships/invite/",
        {"email": "new@scalevexo.test", "role": "sales_rep", "team": str(uuid.uuid4())},
        format="json",
    )
    assert response.status_code == 400
    assert "team" in response.json()["field_errors"]


def test_invitation_to_another_workspaces_team_is_refused(owner, other_workspace):
    from modules.common.tenancy import workspace_context
    from modules.identity.models import Team

    with workspace_context(other_workspace.id):
        rival_team = Team.objects.create(workspace=other_workspace, name="Rival sales")

    response = _client_for(owner).post(
        "/api/v1/memberships/invite/",
        {"email": "new@scalevexo.test", "role": "sales_rep", "team": str(rival_team.id)},
        format="json",
    )
    assert response.status_code == 400


def test_receipt_against_an_unknown_deal_is_refused(owner, workspace, contact_factory):
    from modules.common.tenancy import workspace_context
    from modules.crm.models import Client

    with workspace_context(workspace.id):
        contact = contact_factory(workspace)
        client = Client.objects.create(workspace=workspace, contact=contact, display_name="Acme")

    response = _client_for(owner).post(
        "/api/v1/receipts/",
        {
            "client": str(client.id),
            "opportunity": str(uuid.uuid4()),
            "amount": "100.00",
            "currency": "USD",
            "received_at": date.today().isoformat(),
            "evidence_reference": "Bank ref 1",
        },
        format="json",
    )
    assert response.status_code == 400


def test_health_probes_are_never_throttled(client):
    for _ in range(5):
        assert client.get("/healthz").status_code == 200
