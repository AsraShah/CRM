"""Re-apply the composite tenant key plan after new relationships.

Task gained project and milestone links, and Activity gained its follow-up
task. The plan is idempotent: existing keys are skipped and only the new
relationships receive theirs. Any future relationship between tenant tables
needs a migration like this one; tests.test_tenant_keys names what is missing.
"""

from django.db import migrations

from modules.common import tenant_keys


def forwards(apps, schema_editor):
    for statement in tenant_keys.apply_sql(tenant_keys.plan(apps.get_models())):
        schema_editor.execute(statement)


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0004_composite_tenant_foreign_keys"),
        ("work", "0002_task_milestone_task_project"),
        ("crm", "0005_activity_follow_up_task"),
    ]

    # Reversal is left to 0004's backwards step, which drops every key in the
    # plan; dropping only these three here would leave the plan half-applied.
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
