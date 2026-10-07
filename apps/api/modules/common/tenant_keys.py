"""Composite tenant foreign keys (SVX-TECH-001 section 4.2).

Every relationship between two tenant-owned tables is protected by a foreign key
on (workspace_id, <column>) referencing (workspace_id, id). With it, the database
itself refuses a row that points at another workspace's record, independently of
row-level security and of the application's own checks.

The plan is derived from the models rather than written by hand, so a new
relationship cannot be added without its key: the migration applies the plan,
and tests.test_tenant_keys fails if the database is missing any part of it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

#: Postgres truncates identifiers beyond this length.
MAX_IDENTIFIER = 63


@dataclass(frozen=True)
class CompositeKey:
    table: str
    column: str
    target_table: str

    @property
    def name(self) -> str:
        return _identifier("fk_ws", self.table, self.column)


def unique_name(table: str) -> str:
    return _identifier("uq_ws", table, "id")


def _identifier(prefix: str, table: str, column: str) -> str:
    name = f"{prefix}_{table}_{column}"
    if len(name) <= MAX_IDENTIFIER:
        return name
    digest = hashlib.sha256(name.encode()).hexdigest()[:8]
    return f"{name[: MAX_IDENTIFIER - 9]}_{digest}"


def _is_tenant_model(model) -> bool:
    try:
        field = model._meta.get_field("workspace")
    except Exception:
        return False
    return field.is_relation and field.related_model._meta.db_table == "identity_workspace"


def plan(models) -> list[CompositeKey]:
    """Every (table, column) -> target that needs a composite key.

    A relationship qualifies when both ends are tenant-owned. Links to users,
    to the workspace itself, and to framework tables do not: those are not
    partitioned by workspace.
    """
    keys: set[CompositeKey] = set()
    for model in models:
        if model._meta.proxy or not model._meta.managed or not _is_tenant_model(model):
            continue
        for field in model._meta.concrete_fields:
            if not field.is_relation or not field.many_to_one or field.name == "workspace":
                continue
            target = field.related_model
            if not _is_tenant_model(target):
                continue
            if field.target_field.name != "id":
                continue
            keys.add(
                CompositeKey(
                    table=model._meta.db_table,
                    column=field.column,
                    target_table=target._meta.db_table,
                )
            )
    return sorted(keys, key=lambda k: (k.table, k.column))


def apply_sql(keys: list[CompositeKey]) -> list[str]:
    """Statements that add the keys, idempotently, in a safe order.

    Identifiers come only from Django model metadata (db_table and column
    names), never from a request, so building the DDL as text is safe here.
    DDL cannot take bind parameters for identifiers in any case."""
    statements: list[str] = []
    for target in sorted({key.target_table for key in keys}):
        name = unique_name(target)
        statements.append(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}') "  # noqa: S608
            f'THEN ALTER TABLE "{target}" ADD CONSTRAINT "{name}" UNIQUE (workspace_id, id); '
            f"END IF; END $$;"
        )
    for key in keys:
        statements.append(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{key.name}') "  # noqa: S608
            f'THEN ALTER TABLE "{key.table}" ADD CONSTRAINT "{key.name}" '
            f'FOREIGN KEY (workspace_id, "{key.column}") '
            f'REFERENCES "{key.target_table}" (workspace_id, id) '
            # Deferred, like Django's own foreign keys, so the order rows are
            # written inside one transaction does not change.
            f"DEFERRABLE INITIALLY DEFERRED; END IF; END $$;"
        )
    return statements


def drop_sql(keys: list[CompositeKey]) -> list[str]:
    statements = [
        f'ALTER TABLE "{key.table}" DROP CONSTRAINT IF EXISTS "{key.name}";' for key in keys
    ]
    statements += [
        f'ALTER TABLE "{target}" DROP CONSTRAINT IF EXISTS "{unique_name(target)}";'
        for target in sorted({key.target_table for key in keys})
    ]
    return statements
