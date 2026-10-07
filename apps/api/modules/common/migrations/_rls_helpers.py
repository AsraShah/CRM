"""Shared SQL builders for the row-level security migrations.

Django's migration loader ignores modules whose name starts with an underscore,
so this file sits alongside the migrations without being mistaken for one.

Both 0002 and 0003 build their policies from here, which means the predicate
exists in exactly one place. A subtly different predicate in a later migration
would be an isolation hole that no test would obviously catch.
"""

from __future__ import annotations

# The predicate, written once.
#
# NULLIF turns an unset or empty setting into NULL, and `workspace_id = NULL`
# is NULL, which no row satisfies. A query with no workspace context therefore
# returns nothing: missing context denies access rather than exposing
# everything (SVX-TECH-001 section 5.3).
PREDICATE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"

#: Tables the scheduler identity may enumerate across workspaces. It leases a
#: job and hands the workspace to a scoped worker transaction; it never reads a
#: contact or a deal (section 18.2).
SCHEDULER_TABLES = ("automation_job", "automation_outbox_event")

#: Every tenant-owned table, across all migrations. Kept here so the
#: verification block can assert the whole set at once.
ALL_TENANT_TABLES = (
    # 0002
    "crm_company",
    "crm_contact",
    "crm_lead",
    "crm_opportunity",
    "crm_stage_history",
    "crm_activity",
    "crm_import_batch",
    "crm_import_row",
    "work_task",
    "work_task_reschedule",
    "automation_outbox_event",
    "automation_rule_definition",
    "automation_job",
    "automation_rule_execution",
    "automation_notification",
    "common_audit_event",
    "common_idempotency_record",
    # 0003
    "crm_client",
    "work_project",
    "work_milestone",
    "work_milestone_dependency",
    "work_scope_change",
    "support_ticket",
    "support_ticket_comment",
    "support_ticket_state_history",
    "reporting_cash_receipt",
    "reporting_work_exception",
    "reporting_snapshot",
    "ai_budget_period",
    "ai_usage_reservation",
    "ai_draft",
)

POLICY_SUFFIXES = (
    "tenant_read",
    "tenant_insert",
    "tenant_update",
    "tenant_delete",
    "scheduler_read",
    "scheduler_write",
    "scheduler_insert",
)


def enable_rls(
    table: str,
    *,
    app_role: str,
    append_only: bool = False,
    scheduler_role: str | None = None,
) -> str:
    """Enable and force RLS on one table, with its policies.

    ``FORCE`` matters: without it the table owner bypasses its own policies.
    The application role is deliberately not the owner, but forcing removes the
    whole class of "it worked in development because we connected as postgres"
    mistakes.
    """
    statements = [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;",
        # Read and write are separate policies so an append-only table can
        # receive one without the other.
        f"""
        CREATE POLICY {table}_tenant_read ON {table}
            FOR SELECT TO {app_role}
            USING ({PREDICATE});
        """,
        f"""
        CREATE POLICY {table}_tenant_insert ON {table}
            FOR INSERT TO {app_role}
            WITH CHECK ({PREDICATE});
        """,
    ]

    if not append_only:
        statements.append(
            f"""
            CREATE POLICY {table}_tenant_update ON {table}
                FOR UPDATE TO {app_role}
                USING ({PREDICATE})
                WITH CHECK ({PREDICATE});
            """
        )
        statements.append(
            f"""
            CREATE POLICY {table}_tenant_delete ON {table}
                FOR DELETE TO {app_role}
                USING ({PREDICATE});
            """
        )

    if scheduler_role and table in SCHEDULER_TABLES:
        # The scheduler sees the whole queue, because "which job is due next" is
        # not a question any single workspace can answer. It is constrained by
        # being a different role with grants on these two tables only.
        statements.append(
            f"""
            CREATE POLICY {table}_scheduler_read ON {table}
                FOR SELECT TO {scheduler_role}
                USING (true);
            """
        )
        statements.append(
            f"""
            CREATE POLICY {table}_scheduler_write ON {table}
                FOR UPDATE TO {scheduler_role}
                USING (true) WITH CHECK (true);
            """
        )
        if table == "automation_job":
            statements.append(
                f"""
                CREATE POLICY {table}_scheduler_insert ON {table}
                    FOR INSERT TO {scheduler_role}
                    WITH CHECK (true);
                """
            )

    return "\n".join(statements)


def disable_rls(table: str) -> str:
    drops = "\n".join(
        f"DROP POLICY IF EXISTS {table}_{suffix} ON {table};"
        for suffix in POLICY_SUFFIXES
    )
    return f"{drops}\nALTER TABLE {table} DISABLE ROW LEVEL SECURITY;"


def grants(
    tables: list[str] | tuple[str, ...],
    *,
    append_only: list[str] | tuple[str, ...],
    app_role: str,
) -> str:
    """Grant the application role exactly what each table needs.

    Append-only tables receive INSERT and SELECT but no UPDATE or DELETE. That
    is what makes the audit trail, stage history and ticket state history
    resist ordinary user tampering. It does not, and cannot, protect against a
    compromised database administrator (section 18.3).
    """
    lines: list[str] = []
    for table in tables:
        if table in append_only:
            lines.append(f"GRANT SELECT, INSERT ON {table} TO {app_role};")
            lines.append(f"REVOKE UPDATE, DELETE ON {table} FROM {app_role};")
        else:
            lines.append(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {app_role};"
            )
    return "\n".join(lines)


def verify_all_forced() -> str:
    """Refuse to apply if any tenant table lacks forced row-level security.

    A table added without a policy is readable across workspaces. The failure
    belongs at deployment time, not at the moment a customer notices.
    """
    table_list = ", ".join(f"'{table}'" for table in ALL_TENANT_TABLES)
    return f"""
    DO $$
    DECLARE
        unprotected text;
        missing text;
    BEGIN
        -- Tables that exist but are not protected.
        SELECT string_agg(c.relname, ', ')
          INTO unprotected
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND c.relkind = 'r'
           AND c.relname IN ({table_list})
           AND (c.relrowsecurity IS FALSE OR c.relforcerowsecurity IS FALSE);

        IF unprotected IS NOT NULL THEN
            RAISE EXCEPTION
                'Row-level security is not forced on: %. Refusing to continue.',
                unprotected;
        END IF;

        -- Tables the code expects that the schema does not have. Usually a
        -- renamed table whose policy was never moved with it.
        SELECT string_agg(expected, ', ')
          INTO missing
          FROM unnest(ARRAY[{table_list}]) AS expected
         WHERE NOT EXISTS (
            SELECT 1 FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relname = expected
         );

        IF missing IS NOT NULL THEN
            RAISE EXCEPTION
                'Expected tenant tables are missing: %. Check for a rename.',
                missing;
        END IF;
    END $$;
    """
