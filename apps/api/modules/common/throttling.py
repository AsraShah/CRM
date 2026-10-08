"""Rate limiting (SVX-TECH-001 section 6.1)."""

from __future__ import annotations

from rest_framework.throttling import ScopedRateThrottle


class ActionScopedRateThrottle(ScopedRateThrottle):
    """A scoped throttle that a viewset can confine to its expensive actions.

    A viewset's ``throttle_scope`` otherwise covers every route it serves, so
    the AI allowance of 20 a day would be spent by listing drafts, and the
    import allowance by polling a batch's progress. Views name the actions the
    scope is meant for in ``throttle_scope_actions``; when it is unset the scope
    applies to every action, as ScopedRateThrottle does.
    """

    def allow_request(self, request, view) -> bool:
        actions = getattr(view, "throttle_scope_actions", None)
        if actions is not None and getattr(view, "action", None) not in actions:
            return True
        return super().allow_request(request, view)
