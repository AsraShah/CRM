"""TEST02 / RD05 - tenant isolation.

The requirement is zero cross-tenant disclosure or mutation, and absent context
denies access. These tests exercise the database policy itself, using the
ordinary application role -- not a superuser, which would bypass RLS and make
every assertion here vacuously pass.
"""

from __future__ import annotations

import pytest
from django.db import ProgrammingError, connection

from modules.common.tenancy import (
    MissingWorkspaceContext,
    current_workspace_id,
    no_workspace_context,
    workspace_context,
)
from modules.crm.models import Contact

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.rls]


@pytest.fixture(autouse=True)
def require_unprivileged_role(db):
    """Fail loudly if the test role can bypass the policies.

    Connecting as a superuser or a BYPASSRLS role would make every assertion in
    this module pass without testing anything. That is a worse outcome than a
    failing suite, so it is an error rather than a skip.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
        privileged = cursor.fetchone()[0]

    if privileged:
        pytest.fail(
            "The test database role is a superuser or holds BYPASSRLS, so "
            "row-level security is not enforced and these tests would pass "
            "vacuously. Connect as the unprivileged application role "
            "(see docs/runbooks/setup.md)."
        )


def test_contact_is_invisible_from_another_workspace(workspace, other_workspace, contact_factory):
    with workspace_context(workspace.id):
        contact = contact_factory(workspace, display_name="Ayesha Khan")

    with workspace_context(other_workspace.id):
        # The rival tenant must not see it by any route, including a direct
        # primary-key lookup with the identifier in hand.
        assert Contact.objects.filter(pk=contact.pk).count() == 0
        assert Contact.objects.count() == 0


def test_absent_context_denies_rather_than_exposes(workspace, other_workspace, contact_factory):
    """Missing context must deny, not fall back to "everything"."""
    with workspace_context(workspace.id):
        contact_factory(workspace)
    with workspace_context(other_workspace.id):
        contact_factory(other_workspace)

    with no_workspace_context():
        # NULLIF(...)::uuid is NULL, and workspace_id = NULL matches no row.
        assert Contact.objects.count() == 0


def test_application_query_helper_refuses_without_context(workspace):
    with no_workspace_context(), pytest.raises(MissingWorkspaceContext):
        current_workspace_id()


def test_context_does_not_leak_between_transactions(workspace, other_workspace, contact_factory):
    """A pooled connection must not carry one tenant's context into the next.

    SET LOCAL is discarded at commit, which is exactly why it is used instead of
    SET. This test is the reason that choice is not an implementation detail.
    """
    with workspace_context(workspace.id):
        contact_factory(workspace, display_name="First tenant")

    # A second, independent transaction on the same connection.
    with workspace_context(other_workspace.id):
        assert Contact.objects.count() == 0
        contact_factory(other_workspace, display_name="Second tenant")

    with no_workspace_context():
        assert Contact.objects.count() == 0


def test_cannot_insert_a_record_into_another_workspace(workspace, other_workspace):
    """The INSERT policy's WITH CHECK blocks writing across the boundary."""
    with workspace_context(workspace.id), pytest.raises((ProgrammingError, Exception)):
        Contact.objects.create(
            workspace=other_workspace,
            display_name="Smuggled record",
            source="other",
        )


def test_audit_events_cannot_be_updated_by_the_application_role(workspace, owner):
    """Append-only means the application role holds INSERT and SELECT only.

    This resists ordinary user tampering. It is explicitly *not* a defence
    against a compromised database administrator (section 18.3).
    """
    from modules.common.audit import AuditAction, record_audit
    from modules.common.models import AuditEvent

    with workspace_context(workspace.id):
        event = record_audit(
            action=AuditAction.CREATE,
            entity_type="test.Thing",
            entity_id=None,
            actor=owner.user,
        )
        with pytest.raises(Exception):
            AuditEvent.objects.filter(pk=event.pk).update(reason="rewritten")
