"""Seed the throwaway workspace the Playwright journeys run against.

Development and CI only: refuses to run unless DEBUG is on, so it cannot touch a
production database by accident. Idempotent, so a second run resets the state
the journeys depend on rather than failing.

    E2E_PASSWORD=... python manage.py seed_e2e_workspace

Creates workspace ``e2e`` with an owner, an administrator, a delivery manager
and a sales representative, all active with verified email. The owner's and
administrator's second factors are removed each run, because the journeys enrol
one through allauth's real page.
"""

from __future__ import annotations

import os

from allauth.account.models import EmailAddress
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from modules.identity.models import Membership, MembershipStatus, Role, User, Workspace

USERS = (
    ("e2e-owner@scalevexo.test", "E2E Owner", Role.OWNER),
    ("e2e-admin@scalevexo.test", "E2E Administrator", Role.ADMIN),
    ("e2e-delivery@scalevexo.test", "Dana Delivery", Role.DELIVERY_MANAGER),
    ("e2e-rep@scalevexo.test", "Sam Sales", Role.SALES_REP),
)


class Command(BaseCommand):
    help = "Seed the throwaway e2e workspace (development and CI only)."

    def handle(self, *args, **options) -> None:
        if not settings.DEBUG:
            raise CommandError("Refusing to seed test users with DEBUG off.")
        password = os.environ.get("E2E_PASSWORD", "")
        if len(password) < 12:
            raise CommandError("Set E2E_PASSWORD to at least 12 characters.")

        from allauth.mfa.models import Authenticator

        with transaction.atomic():
            workspace, _ = Workspace.objects.get_or_create(
                slug="e2e", defaults={"name": "E2E Journey", "time_zone": "Asia/Karachi"}
            )
            for email, name, role in USERS:
                user, _ = User.objects.get_or_create(email=email, defaults={"full_name": name})
                user.full_name = name
                user.is_active = True
                user.set_password(password)
                user.save()
                EmailAddress.objects.update_or_create(
                    user=user, email=email, defaults={"verified": True, "primary": True}
                )
                Membership.objects.update_or_create(
                    workspace=workspace,
                    user=user,
                    defaults={
                        "role": role,
                        "status": MembershipStatus.ACTIVE,
                        "suspended_at": None,
                        "mfa_enrolled": False,
                    },
                )
            # The privileged users enrol a second factor through the real page
            # each run, so start them without one.
            Authenticator.objects.filter(
                user__email__in=[
                    email for email, _, role in USERS if role in {Role.OWNER, Role.ADMIN}
                ]
            ).delete()

        self.stdout.write(self.style.SUCCESS("Seeded workspace 'e2e'."))
