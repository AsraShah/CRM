"""Transaction-local workspace context (SVX-TECH-001 section 5.3).

Every request and every worker unit of work follows the same shape:

    begin transaction
      -> set a transaction-local workspace context from validated membership
         (or from the leased job)
      -> perform the work
    commit

The context is set with ``SET LOCAL``, so PostgreSQL discards it when the
transaction ends. That is what stops a pooled connection from carrying one
tenant's context into the next request -- the failure mode RD05 exists to
catch.

Row-level security policies read the same setting. When it is absent,
``current_setting(..., true)`` returns NULL, the policy predicate is NULL and
no rows match: missing context denies access rather than exposing everything.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

from django.db import connections, transaction

WORKSPACE_SETTING = "app.workspace_id"

_current_workspace: ContextVar[uuid.UUID | None] = ContextVar(
    "scalevexo_current_workspace", default=None
)


class MissingWorkspaceContext(RuntimeError):
    """Raised when tenant-scoped work is attempted with no workspace set."""


def current_workspace_id() -> uuid.UUID:
    """Return the active workspace, or fail loudly.

    Callers that can legitimately run without a workspace (the scheduler
    enumerating queue metadata, for example) must not call this.
    """
    workspace_id = _current_workspace.get()
    if workspace_id is None:
        raise MissingWorkspaceContext(
            "No workspace context is active. Tenant-scoped reads and writes "
            "must run inside workspace_context()."
        )
    return workspace_id


def current_workspace_id_or_none() -> uuid.UUID | None:
    return _current_workspace.get()


@contextmanager
def workspace_context(
    workspace_id: uuid.UUID | str, *, using: str = "default"
) -> Iterator[uuid.UUID]:
    """Open a transaction bound to one workspace.

    The caller is responsible for having validated membership (or for holding a
    job lease) before entering. This function deliberately does not perform that
    check itself, so that it cannot be mistaken for an authorisation boundary.
    """
    resolved = uuid.UUID(str(workspace_id))
    token: Token[uuid.UUID | None] = _current_workspace.set(resolved)
    try:
        with transaction.atomic(using=using):
            with connections[using].cursor() as cursor:
                # set_config(..., is_local => true) is the parameterised
                # equivalent of SET LOCAL; SET does not accept bind parameters,
                # and interpolating the value would be an injection point.
                cursor.execute(
                    "SELECT set_config(%s, %s, true)",
                    [WORKSPACE_SETTING, str(resolved)],
                )
            yield resolved
    finally:
        _current_workspace.reset(token)


@contextmanager
def no_workspace_context(*, using: str = "default") -> Iterator[None]:
    """Explicitly clear the workspace context for the enclosing transaction.

    Used by tests asserting that an absent context denies access, and by the
    bootstrap lookup that resolves which workspaces a user belongs to before any
    tenant context can exist.
    """
    token: Token[uuid.UUID | None] = _current_workspace.set(None)
    try:
        with transaction.atomic(using=using):
            with connections[using].cursor() as cursor:
                cursor.execute("SELECT set_config(%s, '', true)", [WORKSPACE_SETTING])
            yield
    finally:
        _current_workspace.reset(token)


def assert_same_workspace(*objects: object) -> uuid.UUID:
    """Reject a relationship that would cross a tenant boundary.

    The database enforces this with composite foreign keys on
    (workspace_id, id). This check exists so that the failure surfaces as a
    domain error at the service boundary instead of an integrity error from the
    driver, and so that a missing composite key is caught by tests rather than
    only in production.
    """
    active = current_workspace_id()

    workspace_ids = set()
    for obj in objects:
        if obj is None:
            continue
        workspace_id = getattr(obj, "workspace_id", None)
        if workspace_id is None:
            raise ValueError(f"{obj!r} carries no workspace_id and cannot be linked.")
        workspace_ids.add(workspace_id)

    if not workspace_ids:
        # Nothing to cross-check. A ticket raised as a purely internal issue
        # has no client, project or milestone, and that is legitimate -- there
        # is simply no relationship to validate.
        return active
    if len(workspace_ids) > 1:
        raise ValueError("Refusing to link records that belong to different workspaces.")

    (workspace_id,) = workspace_ids
    if workspace_id != active:
        raise ValueError("Refusing to link records outside the active workspace context.")
    return workspace_id
