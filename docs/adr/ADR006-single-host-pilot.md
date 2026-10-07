# ADR006 — One host for the internal pilot

- **Status:** Proposed. Binding at G0.
- **Date:** 2026-09-28
- **Reference:** SVX-TECH-001 sections 11, 12.2, 18.1; RD03; RD09

## Context

Operating budget: USD 10–20 per month. Users: 8–10, all internal, all in one
time zone.

## Decision

A single DigitalOcean Basic Droplet — 2 GiB RAM, 1 vCPU, 50 GiB — running
Caddy, Gunicorn, the job worker and PostgreSQL, with encrypted nightly restic
snapshots to a separate storage account.

Planned ceiling, per SVX-TECH-001 section 11.1:

| Item | USD / month | Basis |
| --- | --- | --- |
| Application and database server | 12.00 | Published 2 GiB regular-CPU plan |
| Weekly infrastructure backup | 2.40 | 20% of server price |
| Off-server backup storage | 1.00 | Budget allowance |
| Optional AI | 2.00 | Application-enforced ceiling |
| Operating contingency | 2.60 | Limited allowance for variation |
| **Total** | **20.00** | Pre-tax estimate; validate region and billing |

These are published prices arithmetic, not measured costs. Taxes, exchange
rates, additional storage or egress can exceed the ceiling.

## What this explicitly is not

**This is one failure domain with no automatic failover.** A host failure is an
outage until somebody restores it. That is an acceptable trade for an internal
pilot with a tested restore, and it is **not** an availability guarantee.

No SLA may be sold on this architecture. The targets — RPO 24 hours, staffed
RTO 4 hours — are internal goals measured under a staffed test, not a 24-hour
support promise.

## Consequences

Resources are budgeted explicitly in `infra/docker-compose.yml`: PostgreSQL
768 MB, API 512 MB, worker 384 MB, Caddy 128 MB, leaving headroom for the
operating system. An out-of-memory kill fails the pilot test (section 11.3), so
these limits are part of the design rather than an afterthought.

External monitoring is required. A monitor running on the same host cannot
detect that host being down.

If mandatory costs exceed the ceiling, the response is a revised budget returned
to the CEO. Removing backups or access controls to fit the number is explicitly
not an option.

## Reversal trigger

Separate application and database when measured capacity, recovery needs or
customer commitments require it. Note that a second application server is not
high availability while the only database remains a single unprotected host.

The USD 20 target does **not** transfer to a multi-customer deployment.
Release 3 requires its own load test and cost model.
