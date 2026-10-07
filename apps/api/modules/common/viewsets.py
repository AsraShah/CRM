"""Workspace-scoped view bases (SVX-TECH-001 sections 3.2, 5.3).

Views validate input, resolve the authenticated actor, call a service and
serialize the result. They do not contain business transitions.
"""

from __future__ import annotations

from contextlib import nullcontext

from rest_framework import viewsets
from rest_framework.views import APIView

from modules.common.tenancy import workspace_context
from modules.identity.resolution import MembershipResolutionError, resolve_membership


class WorkspaceContextMixin:
    """Run the request inside one transaction bound to the actor's workspace.

    Wrapping the whole request is what lets a service write the entity change,
    its audit event and its outbox event atomically (section 3.2). A handler
    that must call an external provider does so *after* this transaction
    commits, never while holding business row locks.

    Membership is resolved before the transaction opens. If it cannot be
    resolved -- no session, or no active membership -- the request proceeds
    without a workspace context and the permission class answers 401 or 403.
    Resolving eagerly here (rather than trusting what was true at login) is what
    makes a suspension take effect on the very next request (CRM01).
    """

    #: Key from modules.identity.policy.PERMISSION_MATRIX, or None for
    #: "any active member".
    required_permission: str | None = None

    def dispatch(self, request, *args, **kwargs):
        try:
            membership = resolve_membership(request)
        except MembershipResolutionError:
            membership = None

        request.membership = membership
        context = (
            workspace_context(membership.workspace_id) if membership is not None else nullcontext()
        )
        with context:
            return super().dispatch(request, *args, **kwargs)

    @property
    def workspace_id(self):
        return self.request.membership.workspace_id

    @property
    def actor(self):
        return self.request.user


class WorkspaceMembershipOnlyMixin:
    """Resolve membership but do **not** open a transaction.

    For the one kind of endpoint that calls an external provider during the
    request. Section 3.2 is explicit that provider calls happen after the
    transaction, never while holding business row locks — and there is a second,
    sharper reason here: if the whole request were one transaction, a provider
    timeout would roll back the budget reservation that is meant to survive
    precisely that case (section 10.3).

    Handlers open their own short ``workspace_context`` blocks, so each write
    commits on its own.
    """

    required_permission: str | None = None

    def dispatch(self, request, *args, **kwargs):
        try:
            request.membership = resolve_membership(request)
        except MembershipResolutionError:
            request.membership = None
        return super().dispatch(request, *args, **kwargs)

    @property
    def workspace_id(self):
        return self.request.membership.workspace_id

    @property
    def actor(self):
        return self.request.user


class WorkspaceScopedViewSet(WorkspaceContextMixin, viewsets.GenericViewSet):
    """GenericViewSet whose reads are confined to the active workspace."""

    def get_queryset(self):
        # Row-level security is the real boundary; this is the additional
        # application check required by section 4.2. Ordinary queries also
        # exclude soft-deleted records.
        queryset = super().get_queryset()
        return queryset.filter(
            workspace_id=self.request.membership.workspace_id, deleted_at__isnull=True
        )


class WorkspaceAPIView(WorkspaceContextMixin, APIView):
    """Single-endpoint view running inside the workspace transaction."""
