"""Membership bootstrap (SVX-TECH-001 section 5.3).

This is the narrowly reviewed lookup that runs *before* any workspace context
exists -- it is what decides which context to open, so it cannot itself be
protected by one.

Two rules keep it safe:

* It reads only the identity tables (workspace, membership), never CRM records.
* It never trusts a client-supplied workspace header. A header selects among
  the workspaces the authenticated user already belongs to; it can never
  introduce one.
"""

from __future__ import annotations

import uuid

from django.db import transaction

from modules.common.tenancy import no_workspace_context
from modules.identity.models import Membership, MembershipStatus

WORKSPACE_HEADER = "X-Workspace-ID"


class MembershipResolutionError(Exception):
    """No active membership could be resolved for this request."""


def resolve_membership(request) -> Membership:
    """Return the active membership this request acts under.

    Re-checked on every request, so suspending a member takes effect
    immediately rather than at session expiry (CRM01, TEST01).
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        raise MembershipResolutionError("No authenticated user on this request.")
    if not user.is_active:
        raise MembershipResolutionError("This account is not active.")

    requested = _requested_workspace_id(request)

    # The identity tables carry no RLS policy keyed on app.workspace_id -- they
    # are how a context is chosen. Running with the context explicitly cleared
    # documents that and keeps a stale context from a pooled connection from
    # influencing the lookup.
    with no_workspace_context():
        query = Membership.objects.select_related("workspace", "user").filter(
            user=user, status=MembershipStatus.ACTIVE, workspace__is_active=True
        )
        if requested is not None:
            query = query.filter(workspace_id=requested)
        membership = query.order_by("created_at").first()

    if membership is None:
        raise MembershipResolutionError(
            "This user has no active membership in the requested workspace."
        )
    return membership


def _requested_workspace_id(request) -> uuid.UUID | None:
    raw = request.headers.get(WORKSPACE_HEADER, "").strip()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        # A malformed header is treated as absent rather than as an error: it
        # must not become a way to probe which identifiers are well-formed.
        return None


def active_memberships_for(user) -> list[Membership]:
    """All workspaces this user may act in, for the workspace switcher."""
    with no_workspace_context():
        return list(
            Membership.objects.select_related("workspace")
            .filter(user=user, status=MembershipStatus.ACTIVE, workspace__is_active=True)
            .order_by("workspace__name")
        )


def assert_membership_still_active(membership: Membership) -> None:
    """Re-read a membership under lock before a privileged action.

    Used where the gap between the permission check and the effect matters, for
    example export and rule activation.
    """
    with transaction.atomic():
        current = Membership.objects.select_for_update().get(pk=membership.pk)
        if not current.is_active:
            raise MembershipResolutionError("This membership is no longer active.")
