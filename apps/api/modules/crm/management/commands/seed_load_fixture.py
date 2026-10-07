"""Build the RD03 load-test fixture (SVX-TECH-001 section 11.2).

    E2E_PASSWORD=... python manage.py seed_load_fixture [--scale 1.0]

Creates workspace ``load`` with the acceptance volumes: 10 users, 10,000
contacts, 2,000 opportunities, 100,000 activities, 10,000 tasks and 500
tickets. ``--scale`` shrinks it for a quick smoke run (0.01 is about a minute's
work). Refuses to run with DEBUG off unless ``--allow-non-debug`` is given,
which is how it is used on the dedicated load host and nowhere else.

These are test assumptions, not promised production limits. The fixture is
only half of RD03: the experiment is the k6 run in infra/load against the 2 GiB
reference host, recorded with its raw output.
"""

from __future__ import annotations

import os
import random
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from modules.common.tenancy import workspace_context
from modules.crm.models import (
    Activity,
    ActivityKind,
    Contact,
    EvidenceType,
    Lead,
    LeadStatus,
    Opportunity,
    OpportunityStage,
)
from modules.identity.models import Membership, MembershipStatus, Role, User, Workspace
from modules.support.models import Ticket
from modules.work.models import Task, TaskKind, TaskOrigin

VOLUMES = {
    "contacts": 10_000,
    "opportunities": 2_000,
    "activities": 100_000,
    "tasks": 10_000,
    "tickets": 500,
}
ROLES = (
    [Role.OWNER, Role.SALES_MANAGER]
    + [Role.SALES_REP] * 5
    + [
        Role.DELIVERY_MANAGER,
        Role.DELIVERY_EMPLOYEE,
        Role.ADMIN,
    ]
)
BATCH = 2_000


class Command(BaseCommand):
    help = "Build the RD03 load-test fixture in workspace 'load'."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--scale", type=float, default=1.0)
        parser.add_argument("--allow-non-debug", action="store_true")

    def handle(self, *args, scale: float, allow_non_debug: bool, **options) -> None:
        if not settings.DEBUG and not allow_non_debug:
            raise CommandError("Refusing to seed load data with DEBUG off.")
        password = os.environ.get("E2E_PASSWORD", "")
        if len(password) < 12:
            raise CommandError("Set E2E_PASSWORD (12+ characters) for the load users.")
        if Workspace.objects.filter(slug="load").exists():
            raise CommandError("Workspace 'load' exists. Drop it or use a fresh database.")

        # Reproducible test data, not security: the same fixture every run.
        rng = random.Random(20260929)  # noqa: S311
        now = timezone.now()
        counts = {name: max(1, int(volume * scale)) for name, volume in VOLUMES.items()}

        workspace = Workspace.objects.create(name="Load fixture", slug="load")
        users = []
        for index, role in enumerate(ROLES):
            user = User.objects.create_user(
                email=f"load{index:02d}@scalevexo.test",
                password=password,
                full_name=f"Load User {index:02d}",
            )
            Membership.objects.create(
                workspace=workspace,
                user=user,
                role=role,
                status=MembershipStatus.ACTIVE,
                mfa_enrolled=role in {Role.OWNER, Role.ADMIN},
            )
            users.append(user)
        sellers = users[:7]

        with workspace_context(workspace.id):
            contacts = self._bulk(
                Contact,
                (
                    Contact(
                        workspace=workspace,
                        display_name=f"Prospect {i:05d}",
                        email=f"prospect{i:05d}@example.test",
                        normalized_email=f"prospect{i:05d}@example.test",
                        source="import",
                    )
                    for i in range(counts["contacts"])
                ),
            )
            leads = self._bulk(
                Lead,
                (
                    Lead(
                        workspace=workspace,
                        contact=contact,
                        owner=rng.choice(sellers + [None]),
                        source="import",
                        status=rng.choice(list(LeadStatus.values)),
                    )
                    for contact in contacts
                ),
            )
            stages = [s for s in OpportunityStage.values if s != OpportunityStage.WON]
            opportunities = self._bulk(
                Opportunity,
                (
                    Opportunity(
                        workspace=workspace,
                        contact=leads[i].contact,
                        lead=leads[i],
                        service=f"Service {i % 12}",
                        stage=(stage := rng.choice(stages)),
                        owner=rng.choice(sellers),
                        amount=rng.choice([None, 1500, 4500, 12000]),
                        currency=rng.choice(["USD", "PKR"]),
                        lost_reason="Budget" if stage == OpportunityStage.LOST else "",
                        scope_reference="Proposal" if stage != OpportunityStage.DISCOVERY else "",
                        stage_entered_at=now - timedelta(days=rng.randint(0, 60)),
                    )
                    for i in range(counts["opportunities"])
                ),
            )
            for opp in opportunities:
                if opp.amount is None:
                    opp.currency = ""
            Opportunity.objects.bulk_update(opportunities, ["currency"], batch_size=BATCH)

            self._bulk(
                Activity,
                (
                    Activity(
                        workspace=workspace,
                        contact=(opp := rng.choice(opportunities)).contact,
                        opportunity=opp,
                        kind=rng.choice(list(ActivityKind.values)),
                        outcome="Spoke about scope and timing.",
                        occurred_at=(when := now - timedelta(minutes=rng.randint(0, 90 * 24 * 60))),
                        recorded_at=when,
                        author=opp.owner,
                        evidence_type=EvidenceType.SELF_REPORTED,
                    )
                    for _ in range(counts["activities"])
                ),
            )
            self._bulk(
                Task,
                (
                    Task(
                        workspace=workspace,
                        title=f"Follow up {i:05d}",
                        kind=TaskKind.FOLLOW_UP,
                        owner=(opp := rng.choice(opportunities)).owner,
                        created_by=opp.owner,
                        origin=TaskOrigin.USER,
                        due_at=(due := now + timedelta(hours=rng.randint(-72, 240))),
                        original_due_at=due,
                        calendar_version=workspace.calendar_version,
                        contact=opp.contact,
                        opportunity=opp,
                    )
                    for i in range(counts["tasks"])
                ),
            )
            self._bulk(
                Ticket,
                (
                    Ticket(
                        workspace=workspace,
                        title=f"Issue {i:04d}",
                        origin="client_request",
                        priority=rng.choice(["low", "normal", "high"]),
                        owner=users[8],
                        raised_by=users[7],
                    )
                    for i in range(counts["tickets"])
                ),
            )

        summary = ", ".join(f"{n} {k}" for k, n in counts.items())
        self.stdout.write(self.style.SUCCESS(f"Workspace 'load': 10 users, {summary}."))

    def _bulk(self, model, rows):
        created = []
        batch = []
        for row in rows:
            batch.append(row)
            if len(batch) >= BATCH:
                with transaction.atomic():
                    created += model.objects.bulk_create(batch)
                batch = []
        if batch:
            with transaction.atomic():
                created += model.objects.bulk_create(batch)
        return created
