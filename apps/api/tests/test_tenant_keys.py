"""TEST02 - the database refuses a cross-tenant link on its own.

Row-level security and the services' own checks already stop cross-workspace
references. These tests cover the third layer the specification requires: a
composite (workspace_id, id) foreign key on every tenant-to-tenant relationship.
"""

from __future__ import annotations

import pytest
from django.apps import apps
from django.db import IntegrityError, connection, transaction

from modules.common import tenant_keys
from modules.common.tenancy import workspace_context

pytestmark = pytest.mark.django_db


def test_every_tenant_relationship_has_a_composite_key():
    """Fails when a new model links two tenant tables without a key.

    The migration applies the plan derived from the models; if a later model
    adds a relationship, this names it so the migration can be re-run.
    """
    expected = tenant_keys.plan(apps.get_models())
    assert len(expected) > 20, "The plan should cover the tenant relationships."

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT conname FROM pg_constraint WHERE contype = 'f' AND conname LIKE 'fk_ws_%'"
        )
        present = {row[0] for row in cursor.fetchall()}

    missing = [f"{k.table}.{k.column}" for k in expected if k.name not in present]
    assert not missing, f"Missing composite tenant keys: {missing}"


def test_the_database_refuses_a_row_pointing_at_another_workspace(
    workspace, other_workspace, contact_factory
):
    """Bypass every application check and write the bad row directly."""
    from modules.crm.models import Lead

    with workspace_context(other_workspace.id):
        rival_contact = contact_factory(other_workspace, display_name="Rival's client")

    with workspace_context(workspace.id), pytest.raises(IntegrityError), transaction.atomic():
        Lead.objects.create(workspace=workspace, contact_id=rival_contact.id)
        # The keys are deferred like Django's own, so force the check now.
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
