"""Keep each membership's MFA flag in step with the user's real authenticators.

The permission matrix withholds export, receipts, invitations, member management
and workspace configuration from owners and administrators until
``Membership.mfa_enrolled`` is true (CRM01). allauth owns enrolment, so the flag
has to follow allauth's own events: without this, enrolling a second factor
unlocked nothing, and removing one left the capabilities unlocked.
"""

from __future__ import annotations

from allauth.mfa.models import Authenticator
from allauth.mfa.signals import authenticator_added, authenticator_removed
from allauth.mfa.utils import is_mfa_enabled
from django.dispatch import receiver

from modules.identity.models import Membership


def sync_mfa_enrolment(user) -> bool:
    """Set every membership of this user to whether they have a second factor.

    Recovery codes alone do not count: allauth's is_mfa_enabled() considers only
    real authenticators. Identity tables sit outside the workspace policies, so
    this needs no tenant context (ADR007).
    """
    enrolled = is_mfa_enabled(user, types=[Authenticator.Type.TOTP, Authenticator.Type.WEBAUTHN])
    Membership.objects.filter(user=user).exclude(mfa_enrolled=enrolled).update(
        mfa_enrolled=enrolled
    )
    return enrolled


@receiver(authenticator_added, dispatch_uid="identity.sync_mfa_on_add")
@receiver(authenticator_removed, dispatch_uid="identity.sync_mfa_on_remove")
def _on_authenticator_change(sender, user, **kwargs) -> None:
    sync_mfa_enrolment(user)
