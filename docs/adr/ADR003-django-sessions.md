# ADR003 — Django sessions on one origin

- **Status:** Proposed. Binding at G0.
- **Date:** 2026-09-28
- **Reference:** SVX-TECH-001 sections 5.2, 18.1; T10–T11

## Context

The SPA needs to authenticate against the API. The common default is a JWT held
in browser storage.

## Decision

Server-side Django sessions, secure `HttpOnly` cookies, CSRF protection on
unsafe methods, with the API and the interface on **one origin** served by
Caddy.

## Why not a token in browser storage

1. **Revocation.** Suspending a member must end their access immediately
   (CRM01, TEST01). A server-side session is deleted and the next request
   fails. A self-contained JWT stays valid until it expires, which means either
   a short expiry with constant refresh, or a revocation list — a session store
   by another name.
2. **Exposure.** Anything readable by JavaScript is readable by injected
   JavaScript. An `HttpOnly` cookie is not.
3. **Maturity.** allauth provides login, password reset, email verification and
   MFA, reviewed and maintained. Hand-rolling equivalents is exactly the
   custom cryptography that section 5.2 warns against.

Same-origin removes CORS configuration entirely; there is no permissive
development setting that can leak into production.

## Consequences

The session cookie is `SameSite=Lax`, so cross-site POSTs are rejected by the
browser before CSRF checks apply. The CSRF cookie is deliberately **not**
`HttpOnly` — the SPA must read it to echo into `X-CSRFToken`, which is the
standard double-submit pattern and does not weaken the session cookie.

Session lookups hit the database on every request. At pilot scale that is
immaterial. `_invalidate_sessions_for` scans the session table, which is noted
in the code as needing a per-user index if the user count grows.

## Reversal trigger

Native mobile applications need a different identity mechanism. That decision is
taken separately — ADR004 governs whether native apps are built at all — and
must not retrofit token authentication into the browser client as a side effect.
