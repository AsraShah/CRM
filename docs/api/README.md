# API schema

`openapi.json` is **generated**, not hand-written:

```bash
cd apps/api
uv run python manage.py spectacular \
    --file ../../docs/api/openapi.json --validate --fail-on-warn
```

Generate it once the initial migrations exist, then commit it. CI regenerates it
on every run and fails if the committed copy has drifted — the frontend derives
its TypeScript types from this file, so a stale schema silently produces types
that do not match the server.

Regenerate the frontend types after any schema change:

```bash
cd apps/web
pnpm api:types
```

The contract itself is OpenAPI 3.1.1, with cursor pagination (default 50,
maximum 100), ISO 8601 UTC timestamps, UUID identifiers and explicit currency
fields (SVX-TECH-001 section 6.1).
