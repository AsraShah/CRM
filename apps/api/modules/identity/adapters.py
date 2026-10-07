"""allauth adapters (CRM01).

Release 1 is invitation-only. Public self-service registration is an explicit
Release 3 decision, so the adapter closes signup rather than relying on a URL
simply not being linked.
"""

from __future__ import annotations

from allauth.account.adapter import DefaultAccountAdapter


class InvitationOnlyAccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request) -> bool:
        return False

    def get_login_redirect_url(self, request) -> str:
        return "/today"
