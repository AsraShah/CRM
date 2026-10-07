"""Shared fixtures.

Two workspaces exist in almost every fixture set on purpose. A single-tenant
test suite cannot detect a missing workspace filter -- every query looks correct
when there is only one tenant's data to return (TEST02, RD05).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time

import pytest
from django.utils import timezone

from modules.common.calendars import WorkingCalendar, WorkingInterval
from modules.common.tenancy import workspace_context
from modules.identity.models import (
    Membership,
    MembershipStatus,
    Role,
    Team,
    User,
    Workspace,
)

WORKING_INTERVALS = [
    {"start": "09:00", "end": "13:00"},
    {"start": "14:00", "end": "18:00"},
]

TEST_DATABASE_NAME = "test_svx"


@pytest.fixture(scope="session")
def django_db_setup(django_db_blocker):
    """Create and migrate the test database as the *owner* role.

    Django's default test runner would do everything through the ``default``
    connection. That cannot work here: ``default`` is the unprivileged
    application role, which by design cannot create tables — that is precisely
    what makes row-level security a boundary it cannot cross (ADR007).

    So the owner creates the database and runs the migrations, then the tests
    connect as the application role and are subject to the policies. Getting
    this wrong in the obvious direction — running tests as the owner — would
    make every isolation assertion pass without testing anything.
    """
    import psycopg
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connections

    owner = settings.DATABASES["owner"]
    admin_dsn = (
        f"postgresql://{owner['USER']}:{owner['PASSWORD']}@{owner['HOST']}:{owner['PORT']}/postgres"
    )

    def _recreate() -> None:
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DATABASE_NAME}" WITH (FORCE)')
            conn.execute(f'CREATE DATABASE "{TEST_DATABASE_NAME}"')

    _recreate()

    # Point every alias at the freshly created database.
    for alias in settings.DATABASES:
        settings.DATABASES[alias]["NAME"] = TEST_DATABASE_NAME
        connections[alias].settings_dict["NAME"] = TEST_DATABASE_NAME
        connections[alias].close()

    with django_db_blocker.unblock():
        # The runtime roles need to reach the schema the owner is about to
        # populate. Default privileges handle the tables themselves.
        with connections["owner"].cursor() as cursor:
            cursor.execute("GRANT USAGE ON SCHEMA public TO svx_app, svx_scheduler")
            cursor.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE svx_owner IN SCHEMA public "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO svx_app"
            )
            cursor.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE svx_owner IN SCHEMA public "
                "GRANT USAGE, SELECT ON SEQUENCES TO svx_app"
            )
        call_command("migrate", database="owner", verbosity=0, interactive=False)

        # Tests marked `transaction=True` reset state with TRUNCATE, which
        # requires a privilege the application role deliberately does not hold
        # in production. Granting it *only in the test database* keeps the
        # teardown working without weakening what the tests actually verify:
        # TRUNCATE is distinct from UPDATE and DELETE, so the append-only
        # guarantees on audit and history tables are still genuinely tested.
        with connections["owner"].cursor() as cursor:
            cursor.execute("GRANT TRUNCATE ON ALL TABLES IN SCHEMA public TO svx_app")

    yield

    for alias in list(connections):
        connections[alias].close()
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DATABASE_NAME}" WITH (FORCE)')


@pytest.fixture
def karachi_calendar() -> WorkingCalendar:
    return WorkingCalendar(
        time_zone="Asia/Karachi",
        version=1,
        working_weekdays=frozenset({0, 1, 2, 3, 4}),
        intervals=(
            WorkingInterval(time(9, 0), time(13, 0)),
            WorkingInterval(time(14, 0), time(18, 0)),
        ),
    )


@pytest.fixture
def london_calendar() -> WorkingCalendar:
    """A DST-observing zone, so gap and fold cases are actually exercised."""
    return WorkingCalendar(
        time_zone="Europe/London",
        version=1,
        working_weekdays=frozenset({0, 1, 2, 3, 4}),
        intervals=(WorkingInterval(time(9, 0), time(17, 0)),),
    )


def _make_workspace(name: str, slug: str) -> Workspace:
    return Workspace.objects.create(
        name=name,
        slug=slug,
        time_zone="Asia/Karachi",
        working_weekdays=[0, 1, 2, 3, 4],
        working_intervals=WORKING_INTERVALS,
        holidays=[],
        default_currency="USD",
    )


def _make_member(workspace: Workspace, email: str, role: str, **kwargs) -> Membership:
    user = User.objects.create_user(email=email, password="test-password-1234")
    return Membership.objects.create(
        workspace=workspace,
        user=user,
        role=role,
        status=kwargs.pop("status", MembershipStatus.ACTIVE),
        mfa_enrolled=kwargs.pop("mfa_enrolled", role in {Role.OWNER, Role.ADMIN}),
        **kwargs,
    )


@pytest.fixture
def workspace(db) -> Workspace:
    return _make_workspace("ScaleVexo", "scalevexo")


@pytest.fixture
def other_workspace(db) -> Workspace:
    """A second tenant whose records must never be reachable from the first."""
    return _make_workspace("Rival Agency", "rival")


@pytest.fixture
def team(workspace) -> Team:
    return Team.objects.create(workspace=workspace, name="Sales")


@pytest.fixture
def owner(workspace) -> Membership:
    return _make_member(workspace, "ceo@scalevexo.test", Role.OWNER)


@pytest.fixture
def sales_manager(workspace, team) -> Membership:
    return _make_member(workspace, "manager@scalevexo.test", Role.SALES_MANAGER, team=team)


@pytest.fixture
def sales_rep(workspace, team) -> Membership:
    return _make_member(workspace, "rep@scalevexo.test", Role.SALES_REP, team=team)


@pytest.fixture
def other_rep(workspace, team) -> Membership:
    """A colleague, for testing that one rep cannot reach another's records."""
    return _make_member(workspace, "rep2@scalevexo.test", Role.SALES_REP, team=team)


@pytest.fixture
def rival_rep(other_workspace) -> Membership:
    return _make_member(other_workspace, "rep@rival.test", Role.SALES_REP)


@pytest.fixture
def in_workspace(workspace):
    """Run a block inside the first workspace's tenant context."""

    def _enter():
        return workspace_context(workspace.id)

    return _enter


@pytest.fixture
def contact_factory():
    """Create a contact inside whatever workspace context is active."""

    def _create(workspace, **kwargs):
        from modules.crm.models import Contact

        defaults = {
            "display_name": f"Contact {uuid.uuid4().hex[:6]}",
            "source": "referral",
        }
        defaults.update(kwargs)
        return Contact.objects.create(workspace=workspace, **defaults)

    return _create


@pytest.fixture
def frozen_now() -> datetime:
    """A fixed Tuesday 10:00 UTC (15:00 in Karachi), mid-working-day."""
    return datetime(2026, 9, 29, 10, 0, tzinfo=UTC)


@pytest.fixture
def now() -> datetime:
    return timezone.now()
