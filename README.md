# ScaleVexo CRM

A sales-to-delivery system for ScaleVexo's own 8–10 people, built to the
baseline in:

- **SVX-PRD-001** — Product and Delivery Brief (what and why)
- **SVX-TECH-001** — Technical Specification and Research (how)

> **Status: Release 1 feature-complete (CRM01–CRM13), running locally.**
>
> Installed, migrated and executed on 2026-09-29. **256 backend tests pass at
> 87% coverage**, `pip-audit` is clean, the frontend typechecks, tests and
> builds, and a **26-step smoke journey** runs green against a live server —
> lead → deal → conversion → delivery → ticket → reporting.
>
> **Checked against SVX-PRD-001 and SVX-TECH-001 and completed** (2026-10-06).
> Every CRM01–CRM13 behaviour the briefs state now exists in the API and the
> interface, including the four gaps that blocked real use: invitations could
> not be accepted, records could not be edited, rules had no API, and the
> composite tenant keys did not exist. See
> [Specification completion](#specification-completion-2026-10-06).
> **305 backend tests**, **18 frontend tests** and **two Playwright journeys**
> (with axe accessibility and 360-pixel checks) pass against a live server.
>
> **RD02 is done** and recorded in
> [`docs/dependency-baseline.md`](docs/dependency-baseline.md), including the
> **seven defects that only running the code exposed** (login was completely
> broken; the AI budget reservation was rolled back by the very failure it
> exists for) and **12 security advisories found and fixed**.
>
> **Still outstanding:** no deployment, no load test run (RD03 — fixture and k6
> script are ready), no restore drill (RD09), no security assessment, no
> provider integration. RD01 and RD03–RD12 remain as experiments; see
> [`docs/known-issues.md`](docs/known-issues.md).
> Start at [`docs/runbooks/setup.md`](docs/runbooks/setup.md).

---

## What is here

Following the implementation sequence in SVX-TECH-001 section 14:

**Stage 0 — executable baseline**

- Repository and module structure, environment configuration, deployment checks
- Data model with UUID keys, workspace scoping, optimistic versioning
- Row-level security with three separate database roles
- Outbox, durable job queue and audit infrastructure
- Docker Compose, Caddy, encrypted backups, CI
- ADR001–ADR007

**Stages 1–3 — the Release 1 requirements**

| Requirement | What is implemented |
| --- | --- |
| CRM01 Identity and access | Workspaces, memberships, roles, permission matrix, MFA gating, suspension that revokes access without deleting history |
| CRM02 Spreadsheet migration | CSV upload, mapping, preview, reconciled commit, resumable processing, formula-safe error export |
| CRM03 Leads and opportunities | Separate lead status and deal stage, validated transitions, stage history, per-currency totals |
| CRM04 Sales activity | Calls, emails, meetings, LinkedIn touches; self-reported versus provider-confirmed evidence; append-only corrections |
| CRM05 Daily work | Today view, tasks with owner and due time, completion with next action or stop reason, rescheduling that preserves the original deadline |
| CRM06 Rules and alerts | Closed rule schema, working-calendar delays, durable jobs with leases, loop limits, deduplicated alerts |
| CRM07 Conversion and onboarding | Won deal → client → onboarding project in one transaction; idempotent retry; handover accepted or returned with specifics |
| CRM08 Projects and milestones | Submission separate from acceptance, dependency graph with cycle rejection, recorded manager overrides, reopening that preserves evidence, scope changes distinct from defects |
| CRM09 Tickets | Two distinct waiting states, resolution requiring a note *and* a closure test, reopening that preserves the superseded resolution, internal notes that never default to client-visible |
| CRM10 Accountability | Exception queue with observable facts, employee explanation and dispute, manager decision required to close; no scores, no automatic sanctions |
| CRM11 Reporting | Pipeline, won value and cash receipts as three separate figures, per currency, never combined; manual receipt register against evidence |
| CRM12 Selective AI | Reserve-before-dispatch budget with row locking, fenced prompt contract, strict output schema, citation checking, kill switch |
| CRM13 Portability | Owner-only audited ZIP export with stable UUIDs and a manifest; restic backup and restore scripts |

**Not built** — anything beyond Release 1: the email connector and mobile web
(Release 2), external tenancy, billing and the client portal (Release 3). The
`integrations` module exists as an empty package so its boundary is already
fixed.

---

## Layout

```
apps/api/                 Django 5.2 + DRF
  config/                 settings, URLs, deployment checks
  modules/common/         tenancy, audit, outbox, calendars, idempotency, RLS
  modules/identity/       workspaces, memberships, permission matrix
  modules/crm/            contacts, leads, opportunities, activities, import,
                          clients, won-deal conversion
  modules/work/           tasks, projects, milestones, dependencies
  modules/automation/     rules, jobs, worker, notifications
  modules/support/        tickets, comments, visibility
  modules/reporting/      metrics, receipts, exceptions, export
  modules/ai/             budget reservation, prompt contract, validation
  modules/integrations/   (empty — Release 2 connectors)
  tests/                  pytest suite (TEST01–TEST14 coverage)
apps/web/                 React 19 + Vite 7 + TypeScript
infra/                    Compose, Caddy, Postgres roles, backup + restore
docs/adr/                 architecture decision records
docs/runbooks/            setup, recovery
```

---

## The decisions worth knowing before reading the code

**1. Tenant isolation is enforced by the database, from day one.**
Every business table has `workspace_id`; policies are `ENABLE`d *and* `FORCE`d;
three roles exist (`svx_owner` migrates, `svx_app` serves, `svx_scheduler` sees
only the queue). Context is transaction-local, so a pooled connection cannot
carry one tenant's context into the next request. **Absent context denies
access** rather than exposing everything. See
[ADR007](docs/adr/ADR007-shared-schema-rls.md).

**2. Business changes and their consequences commit together.**
Entity change, audit event and outbox event are written in one transaction. A
separate dispatcher turns committed events into jobs. Nothing relies on an
in-memory post-commit callback, which is lost if the process exits at the wrong
moment.

**3. Deadlines are working time, not clock time.**
"Overdue by 2 working hours" on a Friday evening lands on Monday morning. Both
the computed instant and the calendar version that produced it are stored, so a
later calendar edit cannot retroactively move a commitment somebody has already
been measured against.

**4. Evidence labels are set by the server and never overwritten.**
A manually entered activity is always `self_reported`, whatever the client
sends. Only an event carrying a provider reference may be
`provider_confirmed`. Corrections append a linked record; the original is not
rewritten. Audit events and stage history are `INSERT`/`SELECT` only for the
application role.

**5. Unknown is not zero.**
A deal with no agreed value stores `NULL`, and the pipeline shows "Not yet
known". An amount without a currency is rejected outright, because a total that
mixes currencies without a stated basis is worse than no total.

**6. Claiming, doing and approving are three different acts.**
An employee *submits* a milestone with evidence; an authorised reviewer
*accepts* it. Winning a deal creates a handover; delivery *accepts* it, or
returns it naming what is missing. Resolving a ticket needs a resolution note
*and* a closure test result. Each pair is stored separately with its own actor
and timestamp, so nobody can sign off their own work by accident.

**7. Money is three numbers, and AI spend is stopped by the application.**
Open pipeline, won contract value and recorded cash receipts are separate
figures per currency — the dashboard has no combined total, deliberately. AI
reserves its maximum possible cost against a row-locked budget *before*
dispatching, and an ambiguous timeout keeps its reservation rather than
releasing budget the provider may still bill for.

---

## Honest limitations

- **Nothing has been deployed.** It runs on a developer machine. The cost,
  capacity and recovery claims below are still unmeasured, and the Linux half
  of RD02 is not confirmed until CI has run green.
- **Single failure domain.** One host, no automatic failover. Acceptable for an
  internal pilot with a tested restore; not an availability guarantee, and no
  SLA may be sold on it.
- **Audit logs resist ordinary tampering, not a compromised administrator.**
  A superuser bypasses every policy here. Do not describe this as tamper-proof.
- **Cost figures are arithmetic, not measurements.** The USD 20 ceiling comes
  from published prices. RD03 measures the real thing.
- **Licences reviewed once, at this lockfile.** See
  [`docs/licence-review.md`](docs/licence-review.md). No strong copyleft, no
  obligation to publish source on the intended hosted use — but a new
  transitive dependency can change that without the diff making it obvious.
- **No compliance claims.** ISO/IEC/IEEE 29148, OWASP ASVS 5.0 and WCAG 2.2 AA
  are the references used. No conformance assessment has been performed, and
  none may be advertised.
- **AI ships disabled and unevaluated.** `FEATURE_AI_ENABLED` defaults to
  false. The budget arithmetic uses published rates and a deliberately
  pessimistic token estimator; neither has been checked against real usage.
  RD08 — 50 controlled cases including prompt injection hidden in notes — must
  pass before the flag is turned on.
- **The client portal does not exist.** Ticket comments already carry a
  visibility decision so Release 3 inherits correct history rather than
  retrofitting it, but nothing is exposed to clients today.

---

## Interface completion (2026-10-06)

New screens: **Projects** (start, submit with evidence, accept — with a warning
on self-acceptance and a recorded override for unmet dependencies — block with a
next owner, resume, reopen, scope changes versus defects), **Accountability**
(facts, employee explanation or dispute, manager decision), **Settings** (roster,
one-time invitation token, suspend and reinstate, owner export). Existing screens
gained their missing actions: new lead with activity log, status and owner;
opening a deal; closing as won through the conversion flow; ticket creation and
every state transition; new task and reschedule; the receipt register with
correcting entries; and the operational reports with a period selector.

Defects found and fixed along the way, each with a regression test:

1. **Enrolling two-factor authentication unlocked nothing.** No code set
   `Membership.mfa_enrolled` when allauth activated an authenticator, so owners
   and admins stayed locked out of export, receipts, invitations and team
   management. The flag now follows allauth's `authenticator_added` and
   `authenticator_removed` signals (`modules/identity/signals.py`), migration
   `identity/0002` backfills it for anyone already enrolled, and a test drives
   the real login and activation pages with a live TOTP code.
2. **A blocked milestone could never be unblocked.** `blocked → in_progress` was
   permitted but nothing performed it; `start` now resumes blocked work.
3. **Creating a contact with a job title returned 500** — the serializer passed
   `job_title` to a service that did not accept it.
4. **A zero or negative receipt returned 500** from the check constraint instead
   of a 400 naming the field.
5. **The membership roster carried no user id**, which is what every owner
   field takes, so no owner picker could be filled. It now includes `user`.

Interface defects caught by driving it in a browser: the won-deal confirmation
vanished as the deal left the pipeline; a stale selected ticket turned the next
action into a version conflict; a hidden table header widened the page at 360px;
and Vite did not proxy `/static`, so allauth's pages lost their scripts in
development.

---

## Specification completion (2026-10-06)

A requirement-by-requirement check against both briefs found these gaps. All
are now closed, each with tests.

**Blocking**

- **CRM01 — nobody could join.** Invitations were created but never redeemed.
  `/join?token=` now accepts them: a new person sets a password; an existing
  account must sign in first, so a token can never take one over. CSRF is
  enforced on acceptance because it ends in a login.
- **CRM03 — nothing could be edited.** `PATCH` for contacts, leads and deals,
  version-checked and audited. A deal's unknown value can now become known;
  closed deals stay read-only. Qualification records need and fit.
- **CRM06 — rules had no API.** `/rules` lists, edits (as a new version that
  cancels work queued under the old), enables, pauses and simulates against a
  real record; `/alerts` acknowledges and resolves with a note. Screens in
  Settings and on Today.
- **Section 4.2 — no composite tenant keys.** 47 `(workspace_id, id)` foreign
  keys now exist; the database refuses a cross-workspace reference by itself,
  and `test_tenant_keys.py` fails if a new relationship lacks one.

**Behaviour the briefs state**

| Requirement | Added |
| --- | --- |
| CRM02 | Duplicate suggestions and a reviewed merge that moves history and keeps the duplicate's source reference |
| CRM04 | Follow-up created from the activity form; corrections appended from the interface |
| CRM05/06 | Working calendar (days, hours, holidays, time zone) configurable; changes bump the calendar version |
| CRM07 | Customer record page; delivery staff can see the won deal behind their work |
| CRM08 | Milestone cancellation with reason and impact; reviewer send-back; tasks inside milestones; dependencies from the interface |
| CRM09 | Ticket next action |
| CRM11 | Every dashboard figure opens its underlying records, from the same definition, so they reconcile |
| CRM12 | Draft review screen beside its source notes, shown only when AI is enabled |
| Section 1.3 | Navigation as specified: Today, Leads and Deals, Clients, Projects, Tickets, Team, Reports, Settings |

**Handover artifacts added:** Playwright journeys (`apps/web/e2e/`),
[permission matrix](docs/permission-matrix.md), [threat model](docs/threat-model.md),
[user guide](docs/user-guide.md), [known-issues register](docs/known-issues.md),
and the RD03 [load fixture and k6 script](infra/load/).

---

## Commands

```bash
cd apps/api
uv run pytest                           # 305 tests
uv run python manage.py runserver       # API on :8000
uv run python manage.py run_scheduler_tick
uv run --with pip-audit pip-audit --strict
SMOKE_PASSWORD=... uv run python smoke_journey.py   # 26 live checks

cd apps/web
pnpm dev                                # interface on :5173
pnpm test ; pnpm typecheck ; pnpm build

# Browser journeys (API and dev server running)
cd apps/api && E2E_PASSWORD=... uv run python manage.py seed_e2e_workspace
cd apps/web && E2E_PASSWORD=... pnpm e2e

# RD03 load test (on the reference host) - see infra/load/README.md
```

Full instructions, including the one-off database role setup and the initial
migration generation, are in [`docs/runbooks/setup.md`](docs/runbooks/setup.md).
