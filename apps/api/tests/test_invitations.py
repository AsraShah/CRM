"""CRM01 / TEST01 - an invitation is the only way in, so it must work end to end.

Before these endpoints existed an administrator could create an invitation but
nobody could redeem it: signup is closed and nothing accepted the token, so no
one but the bootstrapped owner could ever join a workspace.

These tests use Django's real test client with CSRF enforcement on, because the
acceptance ends in a login and must not be forgeable from another site.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from modules.common.tenancy import workspace_context
from modules.identity import services
from modules.identity.models import Invitation, Membership, MembershipStatus, Role, User

pytestmark = pytest.mark.django_db

STRONG = "correct-horse-battery-staple-41"


def _invite(owner, email: str, role: str = Role.SALES_REP) -> str:
    with workspace_context(owner.workspace_id):
        _, token = services.invite_member(
            actor_membership=owner, actor=owner.user, email=email, role=role
        )
    return token


def _csrf_client() -> Client:
    return Client(enforce_csrf_checks=True)


def _preview(browser: Client, token: str):
    return browser.get("/api/v1/invitations/preview/", {"token": token})


def _accept(browser: Client, payload: dict):
    return browser.post(
        "/api/v1/invitations/accept/",
        payload,
        content_type="application/json",
        HTTP_X_CSRFTOKEN=browser.cookies["csrftoken"].value,
    )


def test_a_new_person_joins_signs_in_and_cannot_reuse_the_token(owner):
    token = _invite(owner, "New.Hire@ScaleVexo.test")
    browser = _csrf_client()

    preview = _preview(browser, token)
    assert preview.status_code == 200
    assert preview.json()["email"] == "new.hire@scalevexo.test"
    assert preview.json()["has_account"] is False
    # The preview set the cookie the acceptance needs.
    assert "csrftoken" in browser.cookies

    response = _accept(browser, {"token": token, "full_name": "New Hire", "password": STRONG})
    assert response.status_code == 201, response.content
    assert response.json()["role"] == Role.SALES_REP
    assert response.json()["status"] == MembershipStatus.ACTIVE

    # Signed in, with the invited role.
    me = browser.get("/api/v1/me/")
    assert me.status_code == 200
    assert me.json()["email"] == "new.hire@scalevexo.test"
    assert me.json()["workspace"]["id"] == str(owner.workspace_id)

    # Single use.
    again = _accept(_fresh_with_cookie(token), {"token": token, "password": STRONG})
    assert again.status_code == 400
    assert Membership.objects.filter(user__email="new.hire@scalevexo.test").count() == 1


def _fresh_with_cookie(token: str) -> Client:
    browser = _csrf_client()
    # The preview refuses a used token but still sets the cookie.
    _preview(browser, token)
    return browser


def test_acceptance_without_a_csrf_token_is_refused(owner):
    token = _invite(owner, "victim@scalevexo.test")
    browser = _csrf_client()
    response = browser.post(
        "/api/v1/invitations/accept/",
        {"token": token, "password": STRONG},
        content_type="application/json",
    )
    assert response.status_code == 403
    assert not User.objects.filter(email="victim@scalevexo.test").exists()


def test_a_token_cannot_take_over_an_existing_account(owner, rival_rep):
    """The rival's account exists. Holding a token must not set its password."""
    token = _invite(owner, rival_rep.user.email)
    browser = _csrf_client()
    assert _preview(browser, token).json()["has_account"] is True

    response = _accept(browser, {"token": token, "password": STRONG})
    assert response.status_code == 403

    rival_rep.user.refresh_from_db()
    assert not rival_rep.user.check_password(STRONG)
    assert not Membership.objects.filter(
        workspace_id=owner.workspace_id, user=rival_rep.user
    ).exists()


def test_an_existing_account_joins_once_signed_in(owner, rival_rep):
    token = _invite(owner, rival_rep.user.email, role=Role.DELIVERY_EMPLOYEE)
    browser = _csrf_client()
    browser.force_login(rival_rep.user)
    _preview(browser, token)

    response = _accept(browser, {"token": token})
    assert response.status_code == 201, response.content
    membership = Membership.objects.get(workspace_id=owner.workspace_id, user=rival_rep.user)
    assert membership.role == Role.DELIVERY_EMPLOYEE
    # The membership elsewhere is untouched.
    rival_rep.refresh_from_db()
    assert rival_rep.status == MembershipStatus.ACTIVE


def test_a_weak_password_is_rejected_with_the_reason(owner):
    token = _invite(owner, "weak@scalevexo.test")
    browser = _csrf_client()
    _preview(browser, token)

    response = _accept(browser, {"token": token, "password": "password"})
    assert response.status_code == 400
    assert response.json()["field_errors"]["password"]
    assert not User.objects.filter(email="weak@scalevexo.test").exists()
    # A failed attempt does not consume the invitation.
    assert Invitation.objects.get(email="weak@scalevexo.test").accepted_at is None


@pytest.mark.parametrize("state", ["expired", "revoked", "unknown"])
def test_unusable_tokens_get_one_indistinguishable_answer(owner, state):
    token = _invite(owner, f"{state}@scalevexo.test")
    invitation = Invitation.objects.get(email=f"{state}@scalevexo.test")
    if state == "expired":
        invitation.expires_at = timezone.now() - timedelta(minutes=1)
        invitation.save(update_fields=["expires_at"])
    elif state == "revoked":
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["revoked_at"])
    else:
        token = "not-a-real-token"

    response = _preview(_csrf_client(), token)
    assert response.status_code == 400
    assert response.json()["message"] == services.INVALID_INVITATION
