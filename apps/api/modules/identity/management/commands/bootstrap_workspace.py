"""Create the first workspace and its owner.

Run once, immediately after migrating. Access is invitation-only, so without
this there is no way in -- and equally, this is the only path that creates an
owner without an invitation.

    python manage.py bootstrap_workspace --name "ScaleVexo" \\
        --slug scalevexo --owner-email ceo@example.com

The password is prompted for interactively, or read from BOOTSTRAP_PASSWORD.
It is never accepted as a command-line argument, because arguments land in the
shell history and in the process list.
"""

from __future__ import annotations

import getpass
import os

from allauth.account.models import EmailAddress
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from modules.identity.models import (
    Membership,
    MembershipStatus,
    Role,
    User,
    Workspace,
)

DEFAULT_INTERVALS = [
    {"start": "09:00", "end": "13:00"},
    {"start": "14:00", "end": "18:00"},
]


class Command(BaseCommand):
    help = "Create the initial workspace and its owner membership."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--name", required=True)
        parser.add_argument("--slug", required=True)
        parser.add_argument("--owner-email", required=True)
        parser.add_argument("--owner-name", default="")
        parser.add_argument("--time-zone", default="Asia/Karachi")
        parser.add_argument("--currency", default="USD")

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        slug = options["slug"].strip().lower()
        if Workspace.objects.filter(slug=slug).exists():
            raise CommandError(f"A workspace with slug {slug!r} already exists.")

        password = os.environ.get("BOOTSTRAP_PASSWORD") or getpass.getpass("Owner password: ")
        if len(password) < 12:
            raise CommandError("The owner password must be at least 12 characters.")

        workspace = Workspace.objects.create(
            name=options["name"],
            slug=slug,
            time_zone=options["time_zone"],
            default_currency=options["currency"].upper()[:3],
            working_weekdays=[0, 1, 2, 3, 4],
            working_intervals=DEFAULT_INTERVALS,
            holidays=[],
        )

        email = User.objects.normalize_email(options["owner_email"]).lower()
        user, created = User.objects.get_or_create(
            email=email,
            defaults={"full_name": options["owner_name"], "is_active": True},
        )
        if created:
            user.set_password(password)
            user.save(update_fields=["password"])

        # Email verification is mandatory, so without a verified address the
        # owner could never sign in -- and there is no one to send them a
        # confirmation link. This account is created by an operator with direct
        # server access, which is a stronger proof of control than clicking a
        # link in an inbox, so it is marked verified here.
        EmailAddress.objects.update_or_create(
            user=user,
            email=email,
            defaults={"verified": True, "primary": True},
        )

        Membership.objects.create(
            workspace=workspace,
            user=user,
            role=Role.OWNER,
            status=MembershipStatus.ACTIVE,
            # The owner must enrol a second factor before privileged actions
            # unlock (CRM01). Bootstrapping does not waive that.
            mfa_enrolled=False,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Created workspace {workspace.name} ({workspace.slug}) with owner {email}."
            )
        )
        self.stdout.write(
            self.style.WARNING(
                "The owner must enrol MFA before membership, rule, export and "
                "receipt actions become available."
            )
        )
