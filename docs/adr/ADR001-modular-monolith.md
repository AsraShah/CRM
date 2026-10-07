# ADR001 — Modular monolith

- **Status:** Proposed. Binding at G0.
- **Date:** 2026-09-28
- **Reference:** SVX-TECH-001 sections 3.1, 18.1

## Context

The pilot serves 8–10 internal users on one 2 GiB virtual server, within a
USD 10–20 monthly operating budget. The system spans sales, delivery, tickets,
automation and reporting — enough surface that a single undifferentiated
codebase would tangle, but nowhere near enough load to justify separate
services.

## Decision

One deployable Django application, divided into modules under
`apps/api/modules/`: identity, crm, work, support, automation, integrations,
reporting and ai.

Module boundaries are enforced by convention, not by network calls:

- Each module exposes **selectors** for authorised reads and **services** for
  state changes.
- Views validate input, resolve the actor, call a service and serialise the
  result. They contain no business transitions.
- Services enforce permissions and invariants themselves, because a service can
  also be reached from a worker, a management command or a test — not only from
  a view that already checked.

## Consequences

A single transaction can span modules, which is what makes the won-deal
conversion (CRM07) atomic: client, project, tasks, audit and outbox events
commit together or not at all. Distributed services would need a saga and
compensating actions for the same guarantee, on a system with ten users.

The cost is that module boundaries can erode silently. A cross-module import of
another module's models is the warning sign; imports should go through the
owning module's selectors and services.

## Reversal trigger

Split a module into its own service when there is **measured** evidence:
resource contention that cannot be resolved in place, or a genuine ownership
split where two teams need independent release cadence. Neither applies to a
team of two engineers.
