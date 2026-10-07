"""HTTP-layer tests.

Everything else in this suite calls services directly. These call the API, so
they exercise the parts nothing else touches: the workspace-context mixin, the
permission classes, the error envelope, and the 401-versus-403 distinction that
the SPA depends on to decide between "sign in again" and "you may not do that".
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from modules.common.tenancy import workspace_context
from modules.crm import services as crm_services
from modules.identity.models import Role

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> APIClient:
    # enforce_csrf_checks stays off here: CSRF is verified by its own test
    # below, and leaving it on would obscure every other assertion.
    return APIClient()


@pytest.fixture
def rep_client(sales_rep) -> APIClient:
    # Its own client instance. Sharing one between role fixtures would mean the
    # second force_login silently replaced the first.
    api = APIClient()
    api.force_login(sales_rep.user)
    return api


@pytest.fixture
def owner_client(owner) -> APIClient:
    api = APIClient()
    api.force_login(owner.user)
    return api


# ---------------------------------------------------------------------------
# Authentication and authorisation
# ---------------------------------------------------------------------------


def test_anonymous_request_is_401_not_403(client):
    """The SPA redirects to login on 401 and shows a denial on 403.

    DRF's default would answer 403 for both, which makes them
    indistinguishable. CsrfSessionAuthentication advertises a challenge
    precisely so this stays a 401.
    """
    response = client.get("/api/v1/today/")
    assert response.status_code == 401


def test_authenticated_but_unauthorised_is_403(rep_client):
    """A representative may not read management reports."""
    response = rep_client.get("/api/v1/reports/overview/")
    assert response.status_code == 403
    assert response.json()["code"] == "not_authorized"


def test_user_without_a_membership_is_rejected(client, db):
    """Authentication is not authorisation: a user with no membership is out.

    403 rather than 401 here, and deliberately so: the credentials are valid,
    so re-authenticating would not help. Telling the SPA to show a login page
    would put the user in a loop.
    """
    from modules.identity.models import User

    stranger = User.objects.create_user(
        email="stranger@example.test", password="test-password-1234"
    )
    client.force_login(stranger)
    assert client.get("/api/v1/today/").status_code == 403


def test_suspended_member_loses_access_immediately(client, owner, sales_rep):
    """CRM01/TEST01: suspension takes effect on the very next request."""
    client.force_login(sales_rep.user)
    assert client.get("/api/v1/today/").status_code == 200

    from modules.identity import services as identity_services

    with workspace_context(owner.workspace_id):
        identity_services.suspend_member(
            actor_membership=owner,
            actor=owner.user,
            membership_id=sales_rep.id,
            reason="Left the company",
        )

    # The session cookie is still in the client, but the membership is gone.
    assert client.get("/api/v1/today/").status_code == 401


def test_csrf_is_enforced_on_unsafe_methods(sales_rep):
    enforcing = APIClient(enforce_csrf_checks=True)
    enforcing.force_login(sales_rep.user)
    response = enforcing.post("/api/v1/contacts/", {"display_name": "No CSRF token"}, format="json")
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Session context
# ---------------------------------------------------------------------------


def test_me_returns_the_actor_and_their_capabilities(rep_client, sales_rep):
    body = rep_client.get("/api/v1/me/").json()

    assert body["email"] == sales_rep.user.email
    assert body["role"] == Role.SALES_REP
    assert body["workspace"]["slug"] == "scalevexo"
    # Drives navigation; the server re-checks every call regardless.
    assert "lead.view" in body["permissions"]
    assert "workspace.export" not in body["permissions"]


def test_owner_without_mfa_does_not_get_privileged_capabilities(client, workspace):
    """Privileged actions stay locked until a second factor is enrolled."""
    from tests.conftest import _make_member

    unenrolled = _make_member(workspace, "ceo-nomfa@scalevexo.test", Role.OWNER, mfa_enrolled=False)
    client.force_login(unenrolled.user)
    permissions = client.get("/api/v1/me/").json()["permissions"]

    assert "workspace.export" not in permissions
    assert "opportunity.transition" in permissions


# ---------------------------------------------------------------------------
# Today
# ---------------------------------------------------------------------------


def test_today_separates_overdue_from_due_today(rep_client, workspace, sales_rep):
    from modules.work import services as work_services

    with workspace_context(workspace.id):
        from modules.crm.models import Contact

        contact = Contact.objects.create(workspace=workspace, display_name="Acme")
        work_services.create_task(
            membership=sales_rep,
            actor=sales_rep.user,
            title="Overdue call",
            due_at=timezone.now() - timedelta(days=1),
            contact=contact,
        )

    body = rep_client.get("/api/v1/today/").json()

    assert len(body["overdue"]) == 1
    assert body["overdue"][0]["title"] == "Overdue call"
    assert body["overdue"][0]["is_overdue"] is True
    assert body["workspace_time_zone"] == "Asia/Karachi"


# ---------------------------------------------------------------------------
# Record-level visibility
# ---------------------------------------------------------------------------


def test_a_representative_cannot_list_a_colleagues_leads(
    client, workspace, sales_rep, other_rep, contact_factory
):
    with workspace_context(workspace.id):
        contact = contact_factory(workspace, display_name="Their prospect")
        crm_services.create_lead(
            membership=other_rep,
            actor=other_rep.user,
            contact=contact,
            owner=other_rep.user,
        )

    client.force_login(sales_rep.user)
    results = client.get("/api/v1/leads/").json()["results"]
    assert results == []

    # The colleague sees their own.
    client.force_login(other_rep.user)
    assert len(client.get("/api/v1/leads/").json()["results"]) == 1


def test_fetching_another_workspaces_record_is_404(
    client, workspace, other_workspace, sales_rep, rival_rep, contact_factory
):
    """A record in another tenant is indistinguishable from one that does not
    exist. Saying "403" would confirm it is there."""
    with workspace_context(other_workspace.id):
        contact = contact_factory(other_workspace, display_name="Rival prospect")

    client.force_login(sales_rep.user)
    response = client.get(f"/api/v1/contacts/{contact.id}/")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Writes, versioning and the error envelope
# ---------------------------------------------------------------------------


def test_creating_a_contact_then_a_deal(rep_client, workspace):
    created = rep_client.post(
        "/api/v1/contacts/",
        {"display_name": "Acme Ltd", "email": "hello@acme.test", "source": "referral"},
        format="json",
    )
    assert created.status_code == 201
    contact_id = created.json()["id"]

    deal = rep_client.post(
        "/api/v1/opportunities/",
        {
            "contact": contact_id,
            "service": "Website build",
            "amount": "5000.00",
            "currency": "USD",
        },
        format="json",
    )
    assert deal.status_code == 201
    assert deal.json()["stage"] == "discovery"


def test_amount_without_currency_returns_the_error_envelope(rep_client, workspace):
    contact_id = rep_client.post(
        "/api/v1/contacts/", {"display_name": "Acme"}, format="json"
    ).json()["id"]

    response = rep_client.post(
        "/api/v1/opportunities/",
        {"contact": contact_id, "service": "Work", "amount": "1200.00"},
        format="json",
    )
    body = response.json()

    assert response.status_code == 400
    assert body["code"] == "validation_failed"
    assert "currency" in body["field_errors"]
    # Every error carries a request id for correlation with the logs.
    assert body["request_id"]


def test_stale_version_returns_409_with_the_current_version(
    rep_client, workspace, sales_rep, contact_factory
):
    with workspace_context(workspace.id):
        contact = contact_factory(workspace)
        deal = crm_services.create_opportunity(
            membership=sales_rep,
            actor=sales_rep.user,
            contact=contact,
            service="Retainer",
            amount=Decimal("1000"),
            currency="USD",
        )

    ok = rep_client.post(
        f"/api/v1/opportunities/{deal.id}/transition/",
        {"target_stage": "qualified", "expected_version": deal.version},
        format="json",
    )
    assert ok.status_code == 200

    stale = rep_client.post(
        f"/api/v1/opportunities/{deal.id}/transition/",
        {"target_stage": "lost", "expected_version": deal.version, "lost_reason": "x"},
        format="json",
    )
    body = stale.json()

    assert stale.status_code == 409
    assert body["code"] == "version_conflict"
    # The client reloads using this rather than guessing.
    assert body["current_version"] > deal.version


def test_won_is_refused_through_the_generic_transition(
    owner_client, workspace, owner, contact_factory
):
    """Winning creates a client and onboarding, so it must go through convert."""
    with workspace_context(workspace.id):
        contact = contact_factory(workspace)
        deal = crm_services.create_opportunity(
            membership=owner, actor=owner.user, contact=contact, service="Work"
        )

    response = owner_client.post(
        f"/api/v1/opportunities/{deal.id}/transition/",
        {"target_stage": "won", "expected_version": deal.version},
        format="json",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "use_convert_endpoint"


def test_activity_is_always_labelled_self_reported(rep_client, workspace):
    """A client cannot declare its own entry provider-confirmed (CRM04)."""
    contact_id = rep_client.post(
        "/api/v1/contacts/", {"display_name": "Acme"}, format="json"
    ).json()["id"]

    response = rep_client.post(
        "/api/v1/activities/",
        {
            "contact": contact_id,
            "kind": "call",
            "occurred_at": (timezone.now() - timedelta(hours=1)).isoformat(),
            "outcome": "Spoke to them",
            # Deliberately attempting to forge the evidence label.
            "evidence_type": "provider_confirmed",
            "provider_reference": "forged-reference",
        },
        format="json",
    )
    body = response.json()

    assert response.status_code == 201
    assert body["evidence_type"] == "self_reported"
    assert body["provider_reference"] == ""


# ---------------------------------------------------------------------------
# Reporting and export
# ---------------------------------------------------------------------------


def test_overview_keeps_the_three_money_figures_separate(owner_client, workspace):
    body = owner_client.get("/api/v1/reports/overview/").json()

    assert "open_pipeline_value" in body
    assert "won_contract_value" in body
    assert "cash_received" in body
    assert body["basis"]["currencies_combined"] is False


def test_export_is_owner_only(rep_client, owner_client):
    assert rep_client.post("/api/v1/export/").status_code == 403

    response = owner_client.post("/api/v1/export/")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    assert "attachment" in response["Content-Disposition"]


def test_health_probes_need_no_authentication(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_roster_exposes_user_ids_for_owner_pickers(rep_client, owner, sales_rep, rival_rep):
    """Owner and assignee fields take user ids, so the roster must carry them.

    Only this workspace's members appear: the roster is the source of every
    owner picker, and a rival tenant's people must never be offered.
    """
    response = rep_client.get("/api/v1/memberships/")
    assert response.status_code == 200
    rows = response.json()["results"]
    by_email = {row["email"]: row for row in rows}

    assert by_email[owner.user.email]["user"] == str(owner.user_id)
    assert by_email[sales_rep.user.email]["user"] == str(sales_rep.user_id)
    assert rival_rep.user.email not in by_email


def test_contact_created_over_http_keeps_its_job_title(rep_client):
    """The create serializer accepts job_title, so the service must take it.

    It used to be passed straight through to create_contact(), which had no such
    parameter: any request that included a job title failed with a 500.
    """
    response = rep_client.post(
        "/api/v1/contacts/",
        {"display_name": "Amina Shah", "email": "amina@example.test", "job_title": "CTO"},
        format="json",
    )
    assert response.status_code == 201, response.content
    assert response.json()["job_title"] == "CTO"
