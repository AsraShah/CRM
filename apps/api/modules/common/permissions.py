"""DRF permission classes (SVX-TECH-001 section 5.2).

Permissions combine role, team and record assignment. The default is deny for
exports, receipt entry, rule editing, invitation management and employee
exception review.

These classes are a first gate at the view boundary. They are not the only
gate: services re-check authorisation, because a service can also be reached
from a worker, a management command or a test.
"""

from __future__ import annotations

from rest_framework.permissions import BasePermission


class IsWorkspaceMember(BasePermission):
    """Require an authenticated user with an active membership.

    ``request.membership`` is resolved by
    ``modules.identity.resolution.resolve_membership`` in the view's initial
    step, which also re-checks that the membership is still active. Checking it
    on every request -- rather than trusting what was true at login -- is what
    makes suspension take effect immediately.
    """

    message = "You do not have an active membership in this workspace."

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        membership = getattr(request, "membership", None)
        return membership is not None and membership.is_active


class HasWorkspacePermission(IsWorkspaceMember):
    """Require a named capability from the workspace permission matrix.

    Views declare ``required_permission`` (a key from
    ``modules.identity.policy.PERMISSION_MATRIX``). An unknown key denies,
    so a typo fails closed.
    """

    def has_permission(self, request, view) -> bool:
        if not super().has_permission(request, view):
            return False

        from modules.identity.policy import has_permission

        required = getattr(view, "required_permission", None)
        if required is None:
            return True
        return has_permission(request.membership, required)


class IsWorkspaceAdministrator(IsWorkspaceMember):
    """Workspace owner or administrator only.

    Note that this grants configuration authority, not blanket visibility of
    confidential employee information (section 5.2).
    """

    def has_permission(self, request, view) -> bool:
        if not super().has_permission(request, view):
            return False
        from modules.identity.models import Role

        return request.membership.role in {Role.OWNER, Role.ADMIN}


class ReadOnly(BasePermission):
    def has_permission(self, request, view) -> bool:
        return request.method in ("GET", "HEAD", "OPTIONS")
