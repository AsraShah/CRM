# ADR002 — PostgreSQL for records and jobs

- **Status:** Proposed. Binding at G0.
- **Date:** 2026-09-28
- **Reference:** SVX-TECH-001 sections 8.2, 18.1; RD04

## Context

The rule engine needs durable scheduled work: reminders, escalations, alerts.
The obvious reach is Celery with Redis.

On a 2 GiB single host, Redis is a second process competing for memory, a second
thing to back up, a second failure mode — and a second source of truth that can
disagree with the database.

## Decision

PostgreSQL holds both business records and the job queue.

- `OutboxEvent` is written in the **same transaction** as the change it
  describes. A rolled-back change cannot leave an event claiming it happened.
- A dispatcher turns committed outbox events into `Job` rows with a unique
  dedupe key.
- The worker claims jobs with `SELECT ... FOR UPDATE SKIP LOCKED`, stamps a
  random lease token, and completes conditionally on that token still matching.

Crucially, nothing depends on an in-memory `transaction.on_commit` callback.
Such a callback is lost if the process exits between the commit and the
callback — the work is committed, the reminder never scheduled, and nothing
reports a problem.

## Consequences

Exactly-once *effects* for internal database actions come from the unique
`RuleExecution` key, not from the queue: delivery is at-least-once and the
action is idempotent. That distinction is deliberate and must survive any
future migration to a broker.

Queue and business data share one connection budget (20 connections), so worker
concurrency stays at one until measurement says otherwise.

## Reversal trigger

Introduce a dedicated broker when the **measured** job workload exceeds what the
simple worker handles: sustained queue age growth, or claim contention visible
in query plans. A migration must preserve idempotency, job state visibility and
replay safety — a broker that loses those is a downgrade regardless of its
throughput.
