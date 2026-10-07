# Threat model — Release 1 internal pilot

**Status:** working draft for G0 review, 2026-10-06. This records the threats
considered and where each control lives. It is not a security assessment: the
OWASP ASVS 5.0 Level 2 review (SVX-TECH-001 section 5.4) has not been performed,
and no penetration test has been run.

## Scope and assets

One workspace of 8–10 employees on a single host (ADR006). Assets, most
sensitive first:

1. Client and prospect personal data (names, emails, phone numbers, notes).
2. Commercial records: deal values, accepted scope, receipts.
3. Records about employees: activity evidence and accountability exceptions.
4. Credentials: user passwords and second factors, database roles, the
   credential-encryption key, backup keys.
5. The audit log, which is evidence.

## Actors

| Actor | Can do | Primary concern |
| --- | --- | --- |
| Anonymous internet | Reach login, invitation and health endpoints | Credential stuffing, invitation abuse, CSRF |
| Ordinary employee | Their own records via the API | Reaching another employee's or another workspace's records |
| Manager / administrator | Team-wide views, configuration | Misusing visibility; changes without trace |
| Compromised session | Whatever its user can | Silent data export, rule abuse |
| Database superuser / host operator | Everything | Out of scope for application controls — see limitations |

## Threats and controls

| Threat | Control | Where | Verified by |
| --- | --- | --- | --- |
| Cross-tenant read or write | Row-level security, forced, three roles; transaction-local context; absent context denies | `common/migrations/0003`, `common/tenancy.py` | `test_tenant_isolation.py` |
| Cross-tenant reference via a crafted ID | Composite `(workspace_id, id)` foreign keys on all 47 tenant relationships | `common/tenant_keys.py`, migrations 0004–0005 | `test_tenant_keys.py` |
| Reaching a colleague's record by changing an ID | Queryset scoping plus service-level `_assert_can_see` | `crm/views.py`, `crm/services.py` | `test_api.py`, `test_crm_edits.py` |
| Credential stuffing | allauth rate limits; Argon2 hashing; 12-character minimum and common-password check | `config/settings/base.py` | — |
| Login CSRF / forced sign-in via invitation | CSRF on login and on invitation acceptance (explicit, since DRF exempts anonymous) | `identity/views.py` | `test_invitations.py` |
| Invitation token theft or reuse | 256-bit token, stored as SHA-256, single use, 14-day expiry, row locked on use; cannot set an existing account's password | `identity/services.py` | `test_invitations.py` |
| Privileged action without second factor | MFA-gated capabilities; flag follows allauth's real enrolment | `identity/policy.py`, `identity/signals.py` | `test_mfa.py` |
| Suspended employee keeps access | Membership rechecked every request | `common/authentication.py` | `test_api.py` |
| Stale write overwrites a colleague | Expected version on every update; 409 on mismatch | all mutating services | several |
| Duplicate business action on retry | Idempotency keys on conversion and import; unique rule-execution keys | `common/idempotency.py`, `automation/engine.py` | `test_audit_and_idempotency.py` |
| Rule used to do something harmful | Closed Pydantic schema: no code, URLs or regex; no rule can win a deal, accept work, alter a receipt or act on a person; email channel refused | `automation/schemas.py` | `test_rules_api.py` |
| Audit trail edited | Application roles may insert, not update or delete, audit and history tables | RLS migration | `test_tenant_isolation.py` |
| Spreadsheet formula injection in exports | Formula-safe prefixing on export | `crm/normalization.py` | `test_import.py` |
| Bulk data exfiltration | Export is owner-only, MFA-gated and audited | `reporting/export.py` | `test_reporting_and_ai.py` |
| AI prompt injection or overspend | Fenced prompt contract, strict output schema, citation check, row-locked budget reservation, kill switch; AI disabled by default | `ai/` | `test_ai_service.py` (RD08 not yet run) |
| Secrets in logs | Structured logging without request bodies or credentials | `common/logging.py` | — (review) |

## Accepted limitations

- A database superuser or anyone with host root bypasses every control above.
  The audit log resists ordinary tampering; it is not tamper-proof.
- Single host, single failure domain (ADR006). Recovery depends on the restore
  procedure, which has not yet been drilled (RD09).
- No web application firewall or external monitoring is configured.
- Login rate limiting is per allauth defaults and has not been load-tested.
- Seed commands (`seed_e2e_workspace`, `seed_load_fixture`) create known users
  and refuse to run with `DEBUG` off; they must never be enabled in production.

## Open actions before external use

1. Perform and record the ASVS Level 2 review with justified exclusions.
2. RD05 adversarial isolation testing with two tenants (API IDs, reports,
   exports, jobs, pooled connections).
3. RD09 restore drill, keys held outside the host.
4. Confirm an external uptime check and alert routing (owner and deputy).
