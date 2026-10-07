# ADR005 — Deterministic rules with tightly limited AI

- **Status:** Proposed. Binding at G0.
- **Date:** 2026-09-28
- **Reference:** SVX-TECH-001 sections 8, 10, 18.1; CRM12; RD08

## Context

"Keep routine decisions rule-based. Use AI only where interpretation or drafting
provides enough value to justify its cost" (SVX-PRD-001, executive direction).

The pilot AI budget is USD 2 per month inside a USD 20 total.

## Decision

All pipeline logic, assignment, reminders, reporting and employee alerts are
deterministic code. Rules are a **closed, versioned JSON schema** validated by
Pydantic: a fixed set of fields, six operators, four actions.

Rule authors cannot supply Python, JavaScript, SQL, arbitrary URLs or unbounded
regular expressions. A rule is configuration, not code.

AI is limited to two optional, server-side, user-invoked endpoints: summarise
selected notes, and draft a follow-up. Neither receives tools or write
authority.

## What no rule can do

By construction, the action set excludes marking a deal won, accepting a
milestone, altering a receipt and suspending an employee. These are decisions
with commercial or employment consequences; they require a person.

## Cost control

The ceiling is enforced **by the application**, not by a provider dashboard
alert — an alert arrives after the spend. Maximum request cost is reserved in a
locked budget row before dispatch and settled afterwards; a request whose
reservation would exceed the remaining budget is denied. SDK-level automatic
retries are disabled, because a retry outside the reservation is unbudgeted
spend.

## Consequences

Core functions continue when AI is disabled, unavailable or over budget. No
employee is blocked from basic work by an optional component.

A visual workflow builder is deferred. Release 1 configures rules through forms
over approved templates, which covers the A01–A08 catalogue.

Instructions found inside notes or received messages are data, never executable
policy. That is a property of the prompt contract and is tested, not assumed.

## Reversal trigger

Widen AI scope only after RD08 passes: 50 controlled cases including prompt
injection hidden in notes, with no cross-client disclosure, no unauthorised
action, no invented commercial facts, and at least 90% acceptable drafts —
**and** an approved budget revision. Schema validity alone does not establish
factual accuracy and is not sufficient evidence.
