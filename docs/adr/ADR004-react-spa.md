# ADR004 — React SPA with responsive design

- **Status:** Proposed. Binding at G0.
- **Date:** 2026-09-28
- **Reference:** SVX-TECH-001 sections 2.1, 15.4, 18.1

## Context

The interface is operational: pipelines, queues, dialogs, filtered tables, live
validation. Users work in it all day, mostly at a desk, sometimes on a phone.

## Decision

React 19 with Vite 7 and TypeScript, served as static assets from the
application origin. Responsive layout covering the core flows down to 360px and
at 200% zoom. No native mobile application in Release 1–3.

CSS Modules with a small token file rather than a design framework: the
interface needs consistency, not a component library, and every dependency is
something the 2 GiB host has to serve.

## Consequences

Assets are built in CI and copied to the server. Running a bundler on a
one-vCPU host is a reliable way to exhaust memory mid-deploy.

Three client-side disciplines are non-negotiable, and are enforced in
`apps/web/src/lib/queries.ts`:

- Query keys include the workspace id, and caches clear on logout and workspace
  change. Otherwise a switch briefly renders the previous tenant from cache.
- No optimistic updates for stage changes, completion acceptance or financial
  records. A pending save must not look complete.
- A failed save retains what the user typed. Nothing is retyped because the
  network dropped.

Accessibility target is WCAG 2.2 AA for the defined core flows, with keyboard,
focus, contrast and screen-reader checks. Automated scans alone are insufficient
and are treated as a floor, not evidence.

## Reversal trigger

Build a native application only when users demonstrate a need the browser cannot
meet: reliable platform notifications, camera-assisted work, or genuine offline
workflows. Reproducing the existing interface in React Native is not such a
need, and funding two mobile implementations to do so is explicitly ruled out.
