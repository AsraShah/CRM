"""Session authentication for the SPA (SVX-TECH-001 section 5.2, 6.2)."""

from __future__ import annotations

from rest_framework import exceptions
from rest_framework.authentication import SessionAuthentication


class CsrfSessionAuthentication(SessionAuthentication):
    """Session auth that enforces CSRF and normalises anonymity to 401.

    DRF's default returns 403 for an unauthenticated request when no
    ``WWW-Authenticate`` challenge exists, which makes "not logged in"
    indistinguishable from "logged in but forbidden". The SPA needs to tell
    those apart: one means redirect to login, the other means show a denial.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, auth = result
        if not user.is_active:
            # Suspension must end an existing session immediately, not at its
            # natural expiry (CRM01, TEST01).
            raise exceptions.AuthenticationFailed("This account is not active.")
        return user, auth

    def authenticate_header(self, request) -> str:
        # Returning a challenge is what makes DRF answer 401 instead of 403.
        return "Session"

    def enforce_csrf(self, request) -> None:
        try:
            super().enforce_csrf(request)
        except exceptions.PermissionDenied as exc:
            raise exceptions.PermissionDenied(
                "CSRF verification failed. Reload the page and retry."
            ) from exc
