"""The workspace permission matrix (SVX-TECH-001 section 5.2).

Default deny. A capability is granted only to the roles listed against it, and
an unknown capability name denies -- a typo must fail closed rather than open.

Record-level visibility (own records, team records, shared records) is a
separate question answered by the selectors in each module; this table answers
only "may this role attempt this kind of action at all".
"""

from __future__ import annotations

from modules.identity.models import Membership, Role

ALL_STAFF = frozenset(
    {
        Role.OWNER,
        Role.ADMIN,
        Role.SALES_MANAGER,
        Role.SALES_REP,
        Role.DELIVERY_MANAGER,
        Role.DELIVERY_EMPLOYEE,
    }
)
MANAGEMENT = frozenset({Role.OWNER, Role.SALES_MANAGER, Role.DELIVERY_MANAGER})
ADMINISTRATION = frozenset({Role.OWNER, Role.ADMIN})

PERMISSION_MATRIX: dict[str, frozenset[str]] = {
    # --- Sales (CRM03, CRM04) ---
    "contact.view": ALL_STAFF,
    "contact.manage": frozenset({Role.OWNER, Role.SALES_MANAGER, Role.SALES_REP}),
    # Merging rewrites which person a history belongs to, so it is a reviewed,
    # manager-level act (CRM02).
    "contact.merge": frozenset({Role.OWNER, Role.SALES_MANAGER}),
    "lead.view": ALL_STAFF,
    "lead.manage": frozenset({Role.OWNER, Role.SALES_MANAGER, Role.SALES_REP}),
    "lead.reassign": frozenset({Role.OWNER, Role.SALES_MANAGER}),
    "opportunity.view": ALL_STAFF,
    "opportunity.manage": frozenset({Role.OWNER, Role.SALES_MANAGER, Role.SALES_REP}),
    "opportunity.transition": frozenset({Role.OWNER, Role.SALES_MANAGER, Role.SALES_REP}),
    # Converting a won deal creates a client and a delivery commitment, so it is
    # held above representative level (CRM07).
    "opportunity.convert": frozenset({Role.OWNER, Role.SALES_MANAGER}),
    "activity.record": ALL_STAFF,
    # --- Work (CRM05) ---
    "task.view": ALL_STAFF,
    "task.manage": ALL_STAFF,
    "task.assign_others": frozenset({Role.OWNER, Role.SALES_MANAGER, Role.DELIVERY_MANAGER}),
    # --- Migration (CRM02) ---
    # Import writes directly into the contact base, so it is a manager action.
    "import.run": frozenset({Role.OWNER, Role.SALES_MANAGER, Role.ADMIN}),
    # --- Delivery (CRM08, CRM09) — Stage 2 ---
    "project.view": ALL_STAFF,
    "project.manage": frozenset({Role.OWNER, Role.DELIVERY_MANAGER}),
    "milestone.submit": ALL_STAFF,
    # Submission and acceptance must be separable actions by separate people
    # (CRM08).
    "milestone.accept": frozenset({Role.OWNER, Role.DELIVERY_MANAGER}),
    "handover.accept": frozenset({Role.OWNER, Role.DELIVERY_MANAGER}),
    "ticket.view": ALL_STAFF,
    "ticket.manage": ALL_STAFF,
    # --- Management (CRM10, CRM11) ---
    "exception.review": MANAGEMENT,
    "report.company": frozenset({Role.OWNER}),
    "report.team": MANAGEMENT,
    "receipt.record": frozenset({Role.OWNER}),
    # --- Administration (CRM01, CRM06, CRM13) ---
    "membership.manage": ADMINISTRATION,
    "invitation.manage": ADMINISTRATION,
    "rule.manage": ADMINISTRATION,
    "workspace.configure": ADMINISTRATION,
    # Export removes data from the access-controlled system, so it stays with
    # the owner (CRM13).
    "workspace.export": frozenset({Role.OWNER}),
    "ai.use": ALL_STAFF,
}

#: Capabilities a privileged user may only exercise with a second factor
#: enrolled (CRM01).
MFA_GATED_PERMISSIONS = frozenset(
    {
        "membership.manage",
        "invitation.manage",
        "rule.manage",
        "workspace.configure",
        "workspace.export",
        "receipt.record",
    }
)


def has_permission(membership: Membership | None, permission: str) -> bool:
    """Answer whether this membership may attempt this capability."""
    if membership is None or not membership.is_active:
        return False

    allowed = PERMISSION_MATRIX.get(permission)
    if allowed is None:
        # Unknown capability: deny, and let the test suite surface the typo.
        return False
    if membership.role not in allowed:
        return False
    if permission in MFA_GATED_PERMISSIONS and membership.role in {
        Role.OWNER,
        Role.ADMIN,
    }:
        return membership.mfa_enrolled
    return True


def permissions_for(membership: Membership | None) -> list[str]:
    """List granted capabilities, for the SPA to drive its navigation.

    The interface uses this to avoid offering actions that would be refused.
    It is a usability aid only -- the server re-checks every call.
    """
    if membership is None:
        return []
    return sorted(name for name in PERMISSION_MATRIX if has_permission(membership, name))
