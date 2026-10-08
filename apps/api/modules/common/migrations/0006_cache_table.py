"""The shared cache table behind throttles and login lockouts.

The same columns ``manage.py createcachetable`` would create, written out so the
table exists whichever settings module runs the migration. It is created by the
owner role; the default privileges in infra/postgres grant the application role
read and write. It holds rate-limit counters only, never tenant data, so it
carries no row-level security policy.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0005_composite_keys_for_task_and_activity_links"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "CREATE TABLE IF NOT EXISTS svx_cache ("
                " cache_key varchar(255) NOT NULL PRIMARY KEY,"
                " value text NOT NULL,"
                " expires timestamp with time zone NOT NULL)",
                "CREATE INDEX IF NOT EXISTS svx_cache_expires ON svx_cache (expires)",
            ],
            reverse_sql=["DROP TABLE IF EXISTS svx_cache"],
        ),
    ]
