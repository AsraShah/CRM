# RD03 load test

Two pieces, both defined by SVX-TECH-001 section 11.2:

1. **Fixture** — `python manage.py seed_load_fixture` (apps/api) builds workspace
   `load`: 10 users, 10,000 contacts, 2,000 deals, 100,000 activities,
   10,000 tasks, 500 tickets. Deterministic (fixed seed). `--scale 0.01` for a
   quick smoke build. Refuses to run with `DEBUG` off unless
   `--allow-non-debug`, which is for the dedicated load host only.
2. **Load** — [`pilot.js`](pilot.js) for k6: ten concurrent sessions, with the
   specification's targets as thresholds (p95 reads < 500 ms, p95 writes < 1 s,
   errors < 1 %).

```bash
E2E_PASSWORD=... python manage.py seed_load_fixture --allow-non-debug
k6 run -e BASE_URL=https://<host> -e PASSWORD=... -e DURATION=30m infra/load/pilot.js
k6 run -e BASE_URL=https://<host> -e PASSWORD=... -e DURATION=24h infra/load/pilot.js  # soak
```

**Status: not yet run.** The fixture has been built at 1 % scale on a developer
machine to prove it is valid against the schema, row-level security and the
composite tenant keys. RD03 itself must run on the 2 GiB reference host with
backups scheduled; a laptop run is not evidence. Record raw k6 output, host
memory and disk, the oldest due job, the commit and the date.
