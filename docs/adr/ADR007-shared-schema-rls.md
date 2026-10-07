# ADR007 — Shared-schema tenancy with row-level security

- **Status:** Proposed. Binding when the technical baseline is accepted at G0.
- **Date:** 2026-09-28
- **Reference:** SVX-TECH-001 sections 5.3, 18.1; RD05; TEST02

## Context

Release 1 serves one workspace: ScaleVexo's own 8–10 people. Release 3
introduces external, paying workspaces whose data must never mix.

The tempting decision is to defer isolation until Release 3, on the grounds
that a single-tenant pilot cannot leak between tenants. That reasoning is
sound and the conclusion is still wrong. Retrofitting isolation means revisiting
every query, every report, every export and every background job written in the
meantime — and being certain none was missed. The cost of that audit is paid
under commercial pressure, with customer data already in the system.

## Decision

Implement shared-schema multi-tenancy with PostgreSQL row-level security **from
Release 1**, and test it against two synthetic tenants from the first
integration test.

Concretely:

1. Every business table carries `workspace_id`.
2. Every relationship between tenant-owned records is protected by a composite
   foreign key on `(workspace_id, target_id)`, so the database refuses a
   cross-tenant link even if application code asks for one.
3. RLS policies are `ENABLE`d **and** `FORCE`d. Forcing matters: without it the
   table owner bypasses its own policies.
4. Three database roles, with distinct credentials:
   - `svx_owner` owns the tables. Used only by `migrate --database=owner`.
   - `svx_app` is the served application. Not the owner, no `BYPASSRLS`.
   - `svx_scheduler` holds queue metadata only — jobs and outbox events. It can
     see that work is due; it cannot read a contact or a deal.
5. Workspace context is set per transaction with `set_config(..., is_local =>
   true)`, from validated membership or from a leased job.
6. The policy predicate is
   `workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid`.

## Why the predicate is written that way

The second argument to `current_setting` is `missing_ok`. With it, an unset
variable yields `NULL` rather than raising. `NULLIF(..., '')` then also maps the
empty string to `NULL`, and `workspace_id = NULL` is `NULL`, which no row
satisfies.

The result: **a query with no workspace context returns nothing.** The failure
mode of forgetting to set context is an empty result and a loud application-level
exception — not a full table scan across every tenant.

## Why transaction-local, not session-local

`SET LOCAL` (equivalently `set_config(..., true)`) is discarded when the
transaction ends. `SET` persists for the connection.

With connection pooling, a session-scoped setting outlives the request that set
it, and the next request to borrow that connection inherits the previous
tenant's context before its own middleware runs. That is a cross-tenant
disclosure caused entirely by a pooling detail, and it is exactly the case RD05
and `test_context_does_not_leak_between_transactions` exist to catch.

## Consequences

**Accepted costs**

- Every read must run inside `workspace_context(...)`. Code that forgets gets an
  empty result, which is safe but initially confusing; `MissingWorkspaceContext`
  is raised by the application layer to make the cause obvious.
- Migrations need a separate credential the running application does not hold,
  which complicates deployment slightly.
- The scheduler needs a third role, because "which job is due next" is a
  question no workspace-scoped role can answer.

**What this does not provide**

- It is not protection against a compromised database administrator. A superuser
  or a `BYPASSRLS` role bypasses every policy here. Section 18.3 states this and
  it must not be presented otherwise in any customer-facing material.
- It is not, by itself, evidence of isolation. RD05 — attacking API identifiers,
  reports, exports, jobs and reused connections with two tenants — is what
  produces that evidence, and it must pass before any external customer data
  enters the system.

## Reversal trigger

Move to a database-per-tenant model if:

- a customer contract requires physical separation or a separate jurisdiction; or
- RD05 shows a class of access path that policies cannot cover; or
- measured contention on shared tables makes per-tenant tuning necessary.

Schema-per-tenant is deliberately not on that list: it multiplies migration cost
by the customer count while providing weaker guarantees than separate databases.

## Verification

- `TEST02` — two-tenant API, export, background-job and connection-reuse tests.
- `RD05` — adversarial isolation testing before external pilots.
- `apps/api/tests/test_tenant_isolation.py` — fails loudly if the test role can
  bypass RLS, rather than passing vacuously.
- The RLS migration's final `DO` block refuses to apply if any tenant table
  ended up without forced row-level security.
