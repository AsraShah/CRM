"""Composite (workspace_id, id) foreign keys between tenant tables.

SVX-TECH-001 section 4.2 requires them; until this migration none existed,
although tenancy.assert_same_workspace documented that they did. The plan is
derived from the historical models at this point in the migration graph, so it
covers exactly the relationships that exist when it runs.

If existing data already links two workspaces, adding the key fails and the
migration stops. That is deliberate: such a row is a tenant-isolation defect to
investigate, not something to paper over.
"""

from django.db import migrations

from modules.common import tenant_keys


def forwards(apps, schema_editor):
    for statement in tenant_keys.apply_sql(tenant_keys.plan(apps.get_models())):
        schema_editor.execute(statement)


def backwards(apps, schema_editor):
    for statement in tenant_keys.drop_sql(tenant_keys.plan(apps.get_models())):
        schema_editor.execute(statement)


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0003_row_level_security"),
        ("identity", "0002_sync_mfa_enrolment"),
        ("crm", "0004_lead_need_and_fit"),
        ("work", "0001_initial"),
        ("automation", "0002_initial"),
        ("support", "0001_initial"),
        ("reporting", "0001_initial"),
        ("ai", "0003_initial"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
