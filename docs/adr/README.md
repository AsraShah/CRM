# Architecture decision records

ADR001–ADR007 are the decisions proposed in SVX-TECH-001 section 18.1. They
become binding when the technical baseline is accepted at **G0**; until then
they are proposals with a stated reversal trigger.

Each record states the decision, why it was taken, and — most importantly —
**what would make us reverse it**. A decision without a reversal trigger tends
to survive on inertia long after the conditions that justified it have gone.

| ADR | Decision | Reversal trigger |
| --- | --- | --- |
| [001](ADR001-modular-monolith.md) | Modular monolith | Measured scaling or ownership needs |
| [002](ADR002-postgres-for-jobs.md) | PostgreSQL for records and jobs | Measured queue contention |
| [003](ADR003-django-sessions.md) | Django sessions on one origin | Native app identity needs |
| [004](ADR004-react-spa.md) | React SPA, responsive | Demonstrated need for a native app |
| [005](ADR005-deterministic-rules.md) | Deterministic rules, limited AI | Evaluation and budget approval |
| [006](ADR006-single-host-pilot.md) | One host for the internal pilot | Capacity or availability commitments |
| [007](ADR007-shared-schema-rls.md) | Shared-schema tenancy with RLS | Validate all access paths before external customers |

## Status

All seven are **Proposed**. None has been accepted, because G0 has not been
held. Recording them now means the first implementation decision is not made
implicitly by whoever writes the first module.
