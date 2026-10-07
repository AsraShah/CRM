"""TEST01 - identity, authorisation and suspension (CRM01)."""

from __future__ import annotations

import pytest

from modules.common.exceptions import NotAuthorized, ValidationFailed
from modules.common.tenancy import workspace_context
from modules.identity import services
from modules.identity.models import MembershipStatus, Role
from modules.identity.policy import has_permission, permissions_for
from modules.identity.resolution import (
    MembershipResolutionError,
    resolve_membership,
)

pytestmark = pytest.mark.django_db


class FakeRequest:
    """Minimal stand-in for a request during membership resolution."""

    def __init__(self, user, workspace_id=None):
        self.user = user
        self.headers = {}
        if workspace_id:
            self.headers["X-Workspace-ID"] = str(workspace_id)


# ---------------------------------------------------------------------------
# Permission matrix
# ---------------------------------------------------------------------------


def test_unknown_permission_denies(sales_rep):
    """A typo must fail closed, not open."""
    assert has_permission(sales_rep, "does.not.exist") is False


def test_representative_cannot_export_or_manage_rules(sales_rep):
    assert has_permission(sales_rep, "workspace.export") is False
    assert has_permission(sales_rep, "rule.manage") is False
    assert has_permission(sales_rep, "receipt.record") is False


def test_administrator_does_not_inherit_owner_reporting(workspace):
    """Workspace administration is configuration authority, not visibility."""
    from tests.conftest import _make_member

    admin = _make_member(workspace, "admin@scalevexo.test", Role.ADMIN)
    assert has_permission(admin, "workspace.configure") is True
    # Company-wide revenue reporting stays with the owner.
    assert has_permission(admin, "report.company") is False


def test_privileged_capabilities_require_mfa(workspace):
    """CRM01: privileged users must hold a second factor."""
    from tests.conftest import _make_member

    owner = _make_member(workspace, "ceo2@scalevexo.test", Role.OWNER, mfa_enrolled=False)
    assert has_permission(owner, "workspace.export") is False
    assert has_permission(owner, "opportunity.transition") is True

    owner.mfa_enrolled = True
    owner.save(update_fields=["mfa_enrolled"])
    assert has_permission(owner, "workspace.export") is True


def test_suspended_membership_holds_no_permissions(sales_rep):
    sales_rep.status = MembershipStatus.SUSPENDED
    sales_rep.save(update_fields=["status"])
    assert permissions_for(sales_rep) == []


# ---------------------------------------------------------------------------
# Membership resolution
# ---------------------------------------------------------------------------


def test_suspension_takes_effect_on_the_next_request(workspace, sales_rep):
    """A suspended user cannot continue through an existing session (TEST01).

    Membership is re-read on every request rather than trusted from login, so
    the change applies immediately rather than at session expiry.
    """
    request = FakeRequest(sales_rep.user)
    assert resolve_membership(request).pk == sales_rep.pk

    sales_rep.status = MembershipStatus.SUSPENDED
    sales_rep.save(update_fields=["status"])

    with pytest.raises(MembershipResolutionError):
        resolve_membership(FakeRequest(sales_rep.user))


def test_workspace_header_cannot_introduce_a_foreign_workspace(sales_rep, other_workspace):
    """The header selects among workspaces the user already belongs to."""
    request = FakeRequest(sales_rep.user, workspace_id=other_workspace.id)
    with pytest.raises(MembershipResolutionError):
        resolve_membership(request)


def test_malformed_workspace_header_is_treated_as_absent(sales_rep):
    request = FakeRequest(sales_rep.user)
    request.headers["X-Workspace-ID"] = "not-a-uuid"
    assert resolve_membership(request).pk == sales_rep.pk


def test_inactive_user_cannot_resolve(sales_rep):
    sales_rep.user.is_active = False
    sales_rep.user.save(update_fields=["is_active"])
    with pytest.raises(MembershipResolutionError):
        resolve_membership(FakeRequest(sales_rep.user))


# ---------------------------------------------------------------------------
# Suspension
# ---------------------------------------------------------------------------


def test_suspension_preserves_business_history(workspace, owner, sales_rep):
    """Suspending revokes access without deleting history (CRM01)."""
    from django.utils import timezone

    from modules.crm.models import Activity, ActivityKind

    with workspace_context(workspace.id):
        from modules.crm.models import Contact

        contact = Contact.objects.create(workspace=workspace, display_name="X")
        activity = Activity.objects.create(
            workspace=workspace,
            contact=contact,
            kind=ActivityKind.CALL,
            occurred_at=timezone.now(),
            recorded_at=timezone.now(),
            author=sales_rep.user,
        )

        services.suspend_member(
            actor_membership=owner,
            actor=owner.user,
            membership_id=sales_rep.id,
            reason="Left the company",
        )
        activity.refresh_from_db()

    sales_rep.refresh_from_db()
    assert sales_rep.status == MembershipStatus.SUSPENDED
    # The original author is preserved, not blanked.
    assert activity.author_id == sales_rep.user_id


def test_suspension_requires_a_reason(workspace, owner, sales_rep):
    with workspace_context(workspace.id), pytest.raises(ValidationFailed):
        services.suspend_member(
            actor_membership=owner,
            actor=owner.user,
            membership_id=sales_rep.id,
            reason="   ",
        )


def test_last_owner_cannot_be_suspended(workspace, owner):
    """Locking everybody out of a workspace is not a recoverable state."""
    from tests.conftest import _make_member

    second_owner = _make_member(workspace, "ceo3@scalevexo.test", Role.OWNER)
    with workspace_context(workspace.id):
        services.suspend_member(
            actor_membership=owner,
            actor=owner.user,
            membership_id=second_owner.id,
            reason="Role change",
        )
        with pytest.raises(ValidationFailed, match="at least one active owner"):
            services.suspend_member(
                actor_membership=second_owner,
                actor=second_owner.user,
                membership_id=owner.id,
                reason="Should be refused",
            )


def test_a_manager_cannot_suspend(workspace, sales_manager, sales_rep):
    with workspace_context(workspace.id), pytest.raises(NotAuthorized):
        services.suspend_member(
            actor_membership=sales_manager,
            actor=sales_manager.user,
            membership_id=sales_rep.id,
            reason="Not permitted",
        )


def test_invitation_stores_only_a_token_hash(workspace, owner):
    """A leaked database row must not yield a usable invitation."""
    import hashlib

    with workspace_context(workspace.id):
        invitation, token = services.invite_member(
            actor_membership=owner,
            actor=owner.user,
            email="new@scalevexo.test",
            role=Role.SALES_REP,
        )
    assert invitation.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in invitation.token_hash


def test_client_role_cannot_be_invited_yet(workspace, owner):
    with workspace_context(workspace.id), pytest.raises(ValidationFailed):
        services.invite_member(
            actor_membership=owner,
            actor=owner.user,
            email="client@example.test",
            role=Role.CLIENT,
        )
