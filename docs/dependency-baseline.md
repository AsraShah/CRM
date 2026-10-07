# Dependency baseline — RD02

**Status:** resolved and executed on 2026-09-29. This is the RD02 deliverable
from SVX-TECH-001 section 17: "Resolve locks on Linux; build UI; migrate
PostgreSQL; test login, MFA and API."

**Caveat on the pass condition.** RD02 specifies resolution *on Linux*. This
run was performed on Windows 11 (x86-64). `uv.lock` is platform-independent and
CI resolves the same file on `ubuntu-24.04`, but the pass condition is not fully
met until CI has run green. Treat this as a strong indication, not the signed
result.

---

## Resolved versions

Locked in `apps/api/uv.lock` and `apps/web/pnpm-lock.yaml`. Both are committed.

### Runtime

| Component | Specified family | Resolved | Notes |
| --- | --- | --- | --- |
| Python | 3.13 | 3.13.15 | Installed via `uv python install` |
| Node.js | 24 LTS | 24.21.0 | Frontend tooling only; no Node app server |
| PostgreSQL | 17 | 17.6 | User-level install, no service |

### Backend

| Package | Specified | Resolved |
| --- | --- | --- |
| django | 5.2 LTS | 5.2.17 |
| djangorestframework | 3.16 | 3.18.1 |
| django-allauth[mfa] | 65.x | 65.19.5 |
| psycopg[binary] | 3.x | 3.3.6 |
| pydantic | 2.x | 2.13.5 |
| drf-spectacular | 0.x | 0.30.0 |
| cryptography | stable | 44.0.3 |
| argon2-cffi | stable | 23.1.0 |
| django-filter | stable | 25.2 |
| gunicorn | stable | 23.0.0 |
| httpx | stable | 0.28.1 |
| phonenumbers | stable | 8.13.55 |
| ruff | 0.8 | 0.16.9 |
| pytest | 8.3 | 8.4.2 |

### Frontend

| Package | Specified | Resolved | Notes |
| --- | --- | --- | --- |
| react / react-dom | 19 | 19.x | |
| vite | 7 | 7.3.6 | |
| typescript | 5.9 | 5.9.3 | |
| vitest | — | **4.1.11** | Pinned; see finding 1 |
| @vitejs/plugin-react | — | **5.2.0** | Pinned; see finding 1 |
| pnpm | 10 | 10.34.6 | |

---

## Findings

Five defects surfaced only because the code was executed. Each is fixed; they
are recorded because the fix is not the interesting part — the reason the test
suite missed them is.

### 1. vitest 2.x is incompatible with Vite 7

`vitest@2` bundles Vite 5 type definitions. With Vite 7 installed, `tsc`
reported a wall of structurally-incompatible `PluginOption` errors that named
neither package.

**Resolution:** vitest 4.1.11 and `@vitejs/plugin-react` 5.2.0. vitest 5 and
plugin-react 6 both declare `vite@^8` and are wrong for this stack. Also note
`vite.config.ts` must import `defineConfig` from `vitest/config`, not `vite`,
or the `test` block is rejected.

**Recheck at G0** whether Vite 8 has shipped; if so the whole set moves
together, not piecemeal.

### 2. Login was completely broken (allauth + custom user model)

`AttributeError: 'User' object has no attribute 'username'`. allauth defaults
`ACCOUNT_USER_MODEL_USERNAME_FIELD` to `"username"`; the user model here is
identified by email and has no such column, so every page that displayed a user
raised — including the login POST.

**Resolution:** set `ACCOUNT_USER_MODEL_USERNAME_FIELD = None` and
`ACCOUNT_USER_MODEL_EMAIL_FIELD = "email"`.

**Why the tests missed it:** they authenticate with `force_login()`, which
bypasses the allauth view entirely. A test suite that never renders the login
page cannot discover that the login page is broken.

### 3. The bootstrapped owner could never sign in

`ACCOUNT_EMAIL_VERIFICATION = "mandatory"`, and `bootstrap_workspace` created a
user with no `EmailAddress` record. Nobody could send a confirmation link,
because the owner is the first account in the system.

**Resolution:** `bootstrap_workspace` now creates a verified, primary
`EmailAddress`. An operator with direct server access is stronger proof of
control than a link in an inbox.

### 4. Budget reservations were rolled back by the failure they exist for

`create_draft` was wrapped in `@transaction.atomic`. On a provider timeout the
service called `budget.mark_uncertain(...)` and then raised — so the rollback
discarded the reservation it had just marked. The "hold the reservation on an
ambiguous outcome" guarantee (section 10.3) did not exist at runtime.

This also violated section 3.2: a provider call was happening inside a business
transaction.

**Resolution:** `create_draft` now runs in phases, each opening its own short
`workspace_context` and committing, with the provider call outside any
transaction. The AI viewset uses `WorkspaceMembershipOnlyMixin` so the request
is not wrapped either. Covered by
`test_a_timeout_holds_the_reservation`.

### 5. UUID relations broke every idempotent response

DRF renders a UUID primary-key *relation* as a `UUID` object, not a string.
Storing such a response in the idempotency record's `JSONField` raised
`TypeError: Object of type UUID is not JSON serializable`, returning 500 from
both `/opportunities/{id}/convert/` and `/imports/{id}/commit/`.

**Resolution:** `idempotency.json_safe()` round-trips the body with
`default=str` before storage.

### Also fixed while running

- **Milestones could never be submitted.** Conversion creates them `planned`,
  and submission requires `in_progress`, but no endpoint moved between the two.
  Added `POST /milestones/{id}/start/`.
- **Duplicate contacts returned 500.** The workspace-unique email index raised
  `IntegrityError` out of the service. Now a 409 `duplicate_contact` naming the
  field that collided.
- **`assert_same_workspace()` rejected having nothing to check**, which is the
  normal case for an internal ticket with no client or project.

---

## Verified on this machine

| Check | Result |
| --- | --- |
| `uv sync` | resolves, no conflicts |
| `ruff check` / `ruff format --check` | clean, 115 files |
| `pytest` | **181 passed** |
| Coverage | 82% overall; domain services ~85% |
| `makemigrations --check` | no changes detected |
| `migrate --database=owner` | applies, RLS verification block passes |
| Tenant isolation tests | pass against the unprivileged role |
| `spectacular --validate --fail-on-warn` | clean, 63 paths, OpenAPI 3.1.1 |
| `pnpm typecheck` | clean |
| `pnpm test` | 5 passed |
| `pnpm build` | 403 kB JS (126 kB gzip), 29 kB CSS |
| `smoke_journey.py` | **26 passed** against a live server |
| `run_scheduler_tick` | dispatched 39 events, raised 1 exception |

---

## Closed since the first pass

| Item | Outcome |
| --- | --- |
| Vulnerability scan | **12 advisories found and fixed.** See below. |
| Licence review | Done — [`docs/licence-review.md`](licence-review.md) |
| Container image digests | All three base images pinned by digest |
| MFA flow | Walked end to end; 11 tests in `tests/test_mfa.py` |
| Domain-service coverage | Every service module now ≥85%; total 87% |
| Linux resolution | Verified at the lockfile level; see the caveat |

### Vulnerabilities found and fixed

`pip-audit --strict` reported **12 advisories across 2 packages**. Both were
held back by my own version constraints, which is the failure mode section 2.3
warns about — a pin that looks conservative is a pin that blocks patches.

| Package | Was | Now | Advisories cleared |
| --- | --- | --- | --- |
| cryptography | 44.0.3 | **50.0.1** | PYSEC-2026-35, -2141, -3552, -3553, -3554, GHSA-537c-gmf6-5ccf |
| pytest | 8.4.2 | **9.1.1** | PYSEC-2026-1845 |

`pyproject.toml` now carries `cryptography>=50.0.0` and `pytest>=9.0.3` with the
advisory IDs in comments, so a future "tidy up the constraints" change cannot
silently reintroduce them. All 256 tests pass on both upgrades.

`pip-audit --strict` is now clean.

### Base images pinned

| Image | Digest | Resolved |
| --- | --- | --- |
| python:3.13-slim-bookworm | `sha256:2325bb28…` | 2026-09-29 |
| postgres:17-alpine | `sha256:b0f9560a…` | 2026-09-29 |
| caddy:2-alpine | `sha256:6aeddd44…` | 2026-09-29 |

Pinning and patching pull against each other, so `infra/refresh-digests.sh`
reports drift (and rewrites with `--write`). Run it on the monthly review and
immediately on a base-image advisory. It deliberately does not commit: a base
image bump is a reviewed change, not housekeeping.

### Two more defects, found by the new tests

**6. A duplicate rule execution poisoned its own transaction.** `execute_job`
caught the `IntegrityError` from the unique `RuleExecution` key and returned
success — but in PostgreSQL a failed statement aborts the whole transaction, so
the worker could not then record that the job had finished. The at-least-once
→ exactly-once guarantee (section 8.2) held for the *effect* and broke on the
bookkeeping. Now wrapped in a savepoint.

**7. `unmet_dependencies` raised whenever a dependency existed.** The
serializer iterated `MilestoneDependency` rows and read `.name` and `.status`
off the link instead of `link.depends_on`. The field exists to explain why
acceptance is unavailable, and it 500'd in exactly that case. The service-level
helper was correct; only an API test with a real dependency could find it.

---

## Still outstanding

- **Linux execution.** The lockfile is universal and carries 128 manylinux
  wheels, including for the native packages (`cryptography`, `psycopg-binary`);
  the only platform markers are `tzdata` and `colorama` conditionals that
  correctly do not install on Linux. So *resolution* for Linux is verified.
  **Execution** is not: WSL is not installed here and installing it needs
  administrator rights. CI on `ubuntu-24.04` closes this.
- **Frontend SBOM.** `sbom.json` covers the Python environment only. See the
  note in the licence review.
- **Transitive licence drift.** Reviewed once, at this lockfile. A new
  transitive dependency can introduce an obligation that nothing in the diff
  makes obvious.
