"""CRM01 / TEST01 — multifactor enrolment and the capabilities it unlocks.

The permission matrix withholds export, receipt entry, rule editing, invitation
management and workspace configuration from owners and administrators until a
second factor is enrolled. That gate was previously asserted only against a
hand-set `mfa_enrolled` boolean, which proves the matrix reads the flag but
proves nothing about whether a real TOTP enrolment ever sets it.

These tests drive allauth's actual MFA machinery: generate a secret, compute a
real time-based code, activate the authenticator, and confirm the capability
becomes available. They also cover the parts that matter when something goes
wrong — a wrong code, a replayed code, and single-use recovery codes.
"""

from __future__ import annotations

import time

import pytest
from allauth.mfa import app_settings as mfa_settings
from allauth.mfa.models import Authenticator
from allauth.mfa.totp.internal import auth as totp_auth
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from modules.identity.models import Role
from modules.identity.policy import MFA_GATED_PERMISSIONS, has_permission

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_totp_replay_cache():
    """allauth records used TOTP codes in the cache, not the database.

    Without clearing it between tests, a code burned by one test would still be
    marked used in the next — and the failure would look like a code-generation
    bug rather than shared state.
    """
    cache.clear()
    yield
    cache.clear()


def _enrol_totp(user) -> str:
    """Enrol a TOTP authenticator through allauth, returning the raw secret."""
    secret = totp_auth.generate_totp_secret()
    totp_auth.TOTP.activate(user, secret)
    return secret


def _current_code(secret: str) -> str:
    """Compute the code for the current time step, as an authenticator app would."""
    counter = int(time.time()) // mfa_settings.TOTP_PERIOD
    return totp_auth.format_hotp_value(totp_auth.hotp_value(secret, counter))


# ---------------------------------------------------------------------------
# The gate itself
# ---------------------------------------------------------------------------


def test_owner_without_a_second_factor_cannot_reach_gated_capabilities(owner):
    owner.mfa_enrolled = False
    owner.save(update_fields=["mfa_enrolled"])

    for permission in sorted(MFA_GATED_PERMISSIONS):
        assert has_permission(owner, permission) is False, permission

    # Ordinary sales work is unaffected: the gate protects privileged actions,
    # it does not lock somebody out of their job (CRM12's principle applied
    # to identity).
    assert has_permission(owner, "opportunity.transition") is True
    assert has_permission(owner, "contact.manage") is True


def test_enrolling_totp_unlocks_the_gated_capabilities(owner):
    owner.mfa_enrolled = False
    owner.save(update_fields=["mfa_enrolled"])
    assert has_permission(owner, "workspace.export") is False

    secret = _enrol_totp(owner.user)
    # The membership flag is what the matrix reads. TOTP.activate() alone does
    # not send allauth's signal, so sync it the way the receiver does; the
    # HTTP test below proves the real enrolment flow triggers it.
    from modules.identity.signals import sync_mfa_enrolment

    assert sync_mfa_enrolment(owner.user) is True
    owner.refresh_from_db()

    assert secret
    for permission in sorted(MFA_GATED_PERMISSIONS):
        assert has_permission(owner, permission) is True, permission


def test_a_representative_is_not_gated_because_they_never_had_the_capability(
    sales_rep,
):
    """The gate is not a second chance at a permission the role lacks."""
    sales_rep.mfa_enrolled = True
    sales_rep.save(update_fields=["mfa_enrolled"])
    assert has_permission(sales_rep, "workspace.export") is False


# ---------------------------------------------------------------------------
# The TOTP mechanism
# ---------------------------------------------------------------------------


def test_a_generated_code_validates_and_a_wrong_one_does_not(owner):
    secret = _enrol_totp(owner.user)
    authenticator = Authenticator.objects.get(user=owner.user)
    wrapper = totp_auth.TOTP(authenticator)

    assert wrapper.validate_code(_current_code(secret)) is True
    assert wrapper.validate_code("000000") is False
    assert wrapper.validate_code("not-a-code") is False


def test_a_code_cannot_be_replayed(owner):
    """A captured code must not work twice within its window.

    allauth marks an accepted code used in the cache for the length of the TOTP
    period. Without that, anybody who read a code over the user's shoulder
    would have the rest of the window to reuse it.

    Worth knowing operationally: because this lives in the cache and not the
    database, a multi-process deployment needs a *shared* cache or the
    protection is per-process. The pilot runs one Gunicorn worker, so the
    locmem default is sufficient today — and this is the reason it would stop
    being sufficient after scaling out.
    """
    secret = _enrol_totp(owner.user)
    authenticator = Authenticator.objects.get(user=owner.user)

    code = _current_code(secret)
    assert totp_auth.TOTP(authenticator).validate_code(code) is True

    authenticator.refresh_from_db()
    assert totp_auth.TOTP(authenticator).validate_code(code) is False


# ---------------------------------------------------------------------------
# Recovery codes
# ---------------------------------------------------------------------------


def test_recovery_codes_are_single_use(owner, settings):
    """CRM01: recovery codes are single-use.

    A recovery code that still worked after being used would be a long-lived
    password that bypasses the second factor entirely.
    """
    from allauth.mfa.recovery_codes.internal import auth as recovery_auth

    _enrol_totp(owner.user)
    generator = recovery_auth.RecoveryCodes.activate(owner.user)
    codes = generator.generate_codes()
    assert len(codes) == settings.MFA_RECOVERY_CODE_COUNT

    authenticator = Authenticator.objects.get(
        user=owner.user, type=Authenticator.Type.RECOVERY_CODES
    )
    wrapper = recovery_auth.RecoveryCodes(authenticator)

    first = codes[0]
    assert wrapper.validate_code(first) is True

    authenticator.refresh_from_db()
    reused = recovery_auth.RecoveryCodes(authenticator)
    assert reused.validate_code(first) is False

    # A different, unused code still works.
    assert reused.validate_code(codes[1]) is True


# ---------------------------------------------------------------------------
# Through the API
# ---------------------------------------------------------------------------


def test_session_context_reports_enrolment_and_permissions(owner):
    """The SPA reads this to explain why an action is unavailable."""
    owner.mfa_enrolled = False
    owner.save(update_fields=["mfa_enrolled"])

    client = APIClient()
    client.force_login(owner.user)

    before = client.get("/api/v1/me/").json()
    assert before["mfa_enrolled"] is False
    assert "workspace.export" not in before["permissions"]

    _enrol_totp(owner.user)
    owner.mfa_enrolled = True
    owner.save(update_fields=["mfa_enrolled"])

    after = client.get("/api/v1/me/").json()
    assert after["mfa_enrolled"] is True
    assert "workspace.export" in after["permissions"]


def test_export_is_refused_until_a_second_factor_exists(owner):
    """The server enforces it; hiding the button is only a courtesy."""
    owner.mfa_enrolled = False
    owner.save(update_fields=["mfa_enrolled"])

    client = APIClient()
    client.force_login(owner.user)
    assert client.post("/api/v1/export/").status_code == 403

    _enrol_totp(owner.user)
    owner.mfa_enrolled = True
    owner.save(update_fields=["mfa_enrolled"])

    response = client.post("/api/v1/export/")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"


def test_suspension_still_wins_over_an_enrolled_factor(owner, sales_rep):
    """MFA is not a bypass: a suspended member is out regardless."""
    from modules.common.tenancy import workspace_context
    from modules.identity import services
    from modules.identity.models import MembershipStatus

    _enrol_totp(sales_rep.user)
    sales_rep.mfa_enrolled = True
    sales_rep.save(update_fields=["mfa_enrolled"])

    client = APIClient()
    client.force_login(sales_rep.user)
    assert client.get("/api/v1/today/").status_code == 200

    with workspace_context(owner.workspace_id):
        services.suspend_member(
            actor_membership=owner,
            actor=owner.user,
            membership_id=sales_rep.id,
            reason="Left the company",
        )

    sales_rep.refresh_from_db()
    assert sales_rep.status == MembershipStatus.SUSPENDED
    assert client.get("/api/v1/today/").status_code == 401


def test_enrolment_timestamp_is_recorded(owner):
    """Useful when reconstructing who could do what, and when."""
    before = timezone.now()
    _enrol_totp(owner.user)
    authenticator = Authenticator.objects.get(user=owner.user)
    assert authenticator.created_at >= before


def test_owner_and_admin_are_the_gated_roles(workspace):
    """The gate applies to privileged roles, per MFA_REQUIRED_ROLES."""
    from tests.conftest import _make_member

    admin = _make_member(workspace, "admin-mfa@scalevexo.test", Role.ADMIN)
    admin.mfa_enrolled = False
    admin.save(update_fields=["mfa_enrolled"])

    assert has_permission(admin, "rule.manage") is False
    assert has_permission(admin, "invitation.manage") is False

    admin.mfa_enrolled = True
    admin.save(update_fields=["mfa_enrolled"])
    assert has_permission(admin, "rule.manage") is True


def test_enrolling_through_the_real_flow_sets_the_flag_and_removal_clears_it(
    owner, django_capture_on_commit_callbacks
):
    """Drive allauth's own pages, the way an owner would.

    Before the identity signal receivers existed, nothing set mfa_enrolled when a
    user activated TOTP: an owner who enrolled stayed locked out of export,
    receipts and team management indefinitely. This goes through login, the
    activation page and a real time-based code, with no hand-set flag.
    """
    from allauth.account.models import EmailAddress
    from allauth.mfa.totp.internal.auth import SECRET_SESSION_KEY
    from django.test import Client

    owner.mfa_enrolled = False
    owner.save(update_fields=["mfa_enrolled"])
    EmailAddress.objects.update_or_create(
        user=owner.user,
        email=owner.user.email,
        defaults={"verified": True, "primary": True},
    )

    browser = Client()
    response = browser.post(
        "/accounts/login/",
        {"login": owner.user.email, "password": "test-password-1234"},
    )
    assert response.status_code == 302, response.content[:500]

    assert browser.get("/accounts/2fa/totp/activate/").status_code == 200
    secret = browser.session[SECRET_SESSION_KEY]

    # allauth sends authenticator_added from transaction.on_commit; the test
    # transaction never commits, so run the callbacks explicitly.
    with django_capture_on_commit_callbacks(execute=True):
        response = browser.post("/accounts/2fa/totp/activate/", {"code": _current_code(secret)})
    assert response.status_code == 302, response.content[:500]

    owner.refresh_from_db()
    assert owner.mfa_enrolled is True
    assert has_permission(owner, "workspace.export") is True

    # Removing the second factor must re-lock the gated capabilities.
    with django_capture_on_commit_callbacks(execute=True):
        response = browser.post("/accounts/2fa/totp/deactivate/")
    assert response.status_code == 302, response.content[:500]
    assert not Authenticator.objects.filter(user=owner.user, type=Authenticator.Type.TOTP).exists()

    owner.refresh_from_db()
    assert owner.mfa_enrolled is False
    assert has_permission(owner, "workspace.export") is False
