# End-to-end journeys

The Playwright journeys live in [`apps/web/e2e/`](../../apps/web/e2e/), beside
the `@playwright/test` installation they must resolve from. SVX-TECH-001 places
them here; Playwright requires the specs and the runner to share one
`node_modules`, so this folder points there instead.

```bash
cd apps/api
E2E_PASSWORD=... python manage.py seed_e2e_workspace   # DEBUG only; idempotent

cd apps/web
pnpm dev                                               # API must also be running
E2E_PASSWORD=... pnpm e2e                              # E2E_CHROME_CHANNEL=chrome to use installed Chrome
```

The journey runs lead → qualification → deal → stage moves → won → handover →
milestone review (including send-back) → ticket lifecycle → receipt → report
drill-down, with axe accessibility checks on each core screen and a 360-pixel
width check. Each run uses unique names, so it can repeat against one database.
