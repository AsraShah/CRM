"""Row-level security and table grants (SVX-TECH-001 section 5.3, ADR007).

This migration is what makes tenant isolation a property of the database rather
than a property of every query an engineer remembers to write.

It runs as the *owner* role (``migrate --database=owner``) and grants a
restricted set of privileges to the *application* role. Three details carry the
weight:

* ``FORCE ROW LEVEL SECURITY`` — without it, the table owner bypasses its own
  policies.
* The predicate treats a missing workspace setting as NULL, so **absent context
  denies access** rather than exposing everything (RD05). It lives in
  ``_rls_helpers`` so it exists in exactly one place.
* Append-only tables receive INSERT and SELECT but no UPDATE or DELETE. That is
  what makes the audit trail resist ordinary user tampering. It does not, and
  cannot, protect against a compromised database administrator — section 18.3
  is explicit, and it must not be claimed otherwise.

Raw SQL, because Django's ORM has no representation for policies or role
grants. It runs last, after every app's tables exist, and ends by verifying
that no tenant table was missed.
"""

from __future__ import annotations

import os

from django.db import migrations

from modules.common.migrations import _rls_helpers as helpers

APP_ROLE = os.environ.get("DATABASE_APP_ROLE", "svx_app")
# Queue-only identity. It can see that work is due and claim it; it cannot read
# a contact, a deal or an activity.
SCHEDULER_ROLE = os.environ.get("DATABASE_SCHEDULER_ROLE", "svx_scheduler")

TENANT_TABLES = list(helpers.ALL_TENANT_TABLES)

# Evidence, not working data. Corrections append a linked record rather than
# editing the original (Journey D).
APPEND_ONLY_TABLES = [
    "crm_stage_history",
    "work_task_reschedule",
    "common_audit_event",
    "support_ticket_state_history",
    "work_scope_change",
    "reporting_snapshot",
]

# Identity tables are deliberately excluded from workspace policies: they are
# how a workspace context is chosen in the first place
# (modules.identity.resolution). They are protected by the narrowly reviewed
# bootstrap lookup and by ordinary grants instead.
IDENTITY_TABLES = [
    "identity_user",
    "identity_workspace",
    "identity_team",
    "identity_membership",
    "identity_invitation",
]

# Django's own tables, plus the cross-workspace heartbeat.
FRAMEWORK_TABLES = [
    "django_session",
    "django_content_type",
    "django_admin_log",
    "django_migrations",
    "auth_permission",
    "auth_group",
    "auth_group_permissions",
    "identity_user_groups",
    "identity_user_user_permissions",
    "account_emailaddress",
    "account_emailconfirmation",
    "mfa_authenticator",
    "automation_scheduler_heartbeat",
]


def _grants() -> str:
    lines = [
        f"GRANT USAGE ON SCHEMA public TO {APP_ROLE};",
        # Needed for Django's internal auto-increment tables.
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE};",
        helpers.grants(
            TENANT_TABLES, append_only=APPEND_ONLY_TABLES, app_role=APP_ROLE
        ),
    ]
    for table in IDENTITY_TABLES + FRAMEWORK_TABLES:
        # IF EXISTS keeps an optional framework table (allauth names vary by
        # release) from failing the whole migration.
        lines.append(
            f"DO $$ BEGIN "
            f"IF to_regclass('public.{table}') IS NOT NULL THEN "
            f"EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} "
            f"TO {APP_ROLE}'; END IF; END $$;"
        )

    # The scheduler identity: queue tables only, and no DELETE. Everything else
    # in the schema stays unreachable to it by simply never being granted.
    lines += [
        f"GRANT USAGE ON SCHEMA public TO {SCHEDULER_ROLE};",
        f"GRANT SELECT, INSERT, UPDATE ON automation_job TO {SCHEDULER_ROLE};",
        f"GRANT SELECT, UPDATE ON automation_outbox_event TO {SCHEDULER_ROLE};",
        f"GRANT SELECT, INSERT, UPDATE ON automation_scheduler_heartbeat "
        f"TO {SCHEDULER_ROLE};",
    ]
    return "\n".join(lines)


class Migration(migrations.Migration):
    # Depends on the final migration of every app, because the policies attach
    # to the tables those migrations create.
    dependencies = [
        ("common", "0002_initial"),
        ("identity", "0001_initial"),
        ("crm", "0002_initial"),
        ("work", "0001_initial"),
        ("automation", "0002_initial"),
        ("support", "0001_initial"),
        ("reporting", "0001_initial"),
        ("ai", "0003_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="\n".join(
                helpers.enable_rls(
                    table,
                    app_role=APP_ROLE,
                    append_only=table in APPEND_ONLY_TABLES,
                    scheduler_role=SCHEDULER_ROLE,
                )
                for table in TENANT_TABLES
            ),
            reverse_sql="\n".join(helpers.disable_rls(t) for t in TENANT_TABLES),
        ),
        migrations.RunSQL(sql=_grants(), reverse_sql=migrations.RunSQL.noop),
        migrations.RunSQL(
            # The last gate: refuses to apply if any tenant table lacks forced
            # row-level security, or if an expected table is missing entirely.
            sql=helpers.verify_all_forced(),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
