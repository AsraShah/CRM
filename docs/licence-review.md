# Licence review

**Requirement:** SVX-TECH-001 section 2.3 — "Create a software bill of
materials and review direct and transitive licenses."

**Reviewed:** 2026-09-29, against `sbom.json` (CycloneDX, 76 components,
generated from the resolved backend environment).

**This is an engineering review, not legal advice.** It records what the
licences are and what obligations they carry on the intended use. If ScaleVexo
ever distributes the software rather than operating it as a service, the
conclusions below change and the review must be redone.

---

## Intended use, which is what the obligations depend on

The pilot is an **internally hosted service**. The code is operated on
ScaleVexo's own server and accessed over HTTPS. It is:

- **not distributed** — no binaries, containers or source go to any third party
- **not modified** — every dependency is used as published, unpatched
- **dynamically linked / imported**, never statically embedded

That combination is what makes the copyleft entries below unproblematic.
Release 3 — external customers on a hosted service — does **not** change this,
because hosting is still not distribution under GPL-family licences. Shipping
an on-premise build to a customer *would*, and would require re-reading this
document first.

---

## Summary

| Category | Count | Obligation on this use |
| --- | --- | --- |
| Permissive (MIT, BSD, Apache-2.0, ISC, PSF, MIT-0) | 67 | Attribution only |
| Weak / file-level copyleft (LGPL, MPL) | 9 | None while unmodified and not distributed |
| Strong copyleft (GPL, AGPL, SSPL) | **0** | — |
| Unresolved | **0** | — |

No component requires ScaleVexo to publish its own source. No component
prohibits commercial use.

---

## Components needing a recorded decision

### psycopg 3.3.6 and psycopg-binary 3.3.6 — LGPL-3.0-only

The PostgreSQL driver, and the most significant entry here.

LGPL permits use in a proprietary application provided the library can be
replaced by the user and is not statically linked into a derived work. The
application imports psycopg as a separate Python package and does not modify or
vendor it, so only the LGPL's own terms attach — to psycopg, not to ScaleVexo's
code.

**Decision:** accepted. **Condition:** if psycopg is ever patched or vendored,
that fork must be published under LGPL. Prefer an upstream issue over a local
patch.

### certifi 2026.7.22, fqdn 1.6.0 — MPL-2.0

MPL-2.0 is *file-level* copyleft: modifying an MPL file obliges publishing that
file, and nothing else. Both are used unmodified.

**Decision:** accepted. **Condition:** do not edit these files in place.

### fido2 2.2.1 — Apache-2.0 / BSD / MPL-2.0

Multi-licensed; the MPL portion carries the weakest-case obligation, handled as
above. Reaches the tree through `django-allauth[mfa]` for WebAuthn support.

**Decision:** accepted.

### chardet 5.2.0 — LGPL-2.1+

Character-encoding detection, pulled in transitively. Same LGPL reasoning as
psycopg.

**Decision:** accepted. Worth noting it is not directly required: the CSV
import does its own bounded encoding attempts
(`utf-8-sig` → `utf-8` → `cp1252`) rather than sniffing, so if the transitive
dependency ever disappears nothing in this codebase breaks.

---

## Two the SBOM reported wrongly

Both were flagged by the automated tally and are clean on inspection. Recorded
because the *next* person running the scan will hit the same two.

### uritemplate 4.2.0 — reported UNDECLARED

Its `METADATA` carries `License: BSD 3-Clause OR Apache-2.0` as a PEP 639
expression, which the SBOM generator did not parse. The package ships
`LICENSE.APACHE` and `LICENSE.BSD` and states the recipient may choose either.

**Actual licence:** BSD-3-Clause OR Apache-2.0. Permissive.

### qrcode 8.2 — reported "Other/Proprietary License"

`METADATA` declares `License: BSD`, and additionally carries a stray
`Classifier: License :: Other/Proprietary License`. The classifier is
inaccurate packaging metadata upstream; the licence file is BSD.

**Actual licence:** BSD. Permissive.

> Neither is a reason to distrust the tooling — it is a reason not to treat a
> licence tally as a finished review. Both entries needed a human to open the
> package.

---

## Frontend

Not covered by `sbom.json`, which is generated from the Python environment
only. The frontend dependency set (React, Vite, TanStack, Radix, Zod, lucide,
date-fns) is uniformly MIT or Apache-2.0, and none of it is shipped to a third
party — Vite bundles it into assets served from ScaleVexo's own origin.

**Outstanding:** generate a CycloneDX SBOM for `apps/web` too, so the review
covers both halves. `pnpm licenses list` is the quick check; a proper SBOM
step belongs in CI alongside the Python one.

---

## Re-running this

```bash
cd apps/api
uv run --with cyclonedx-bom cyclonedx-py environment -o ../../sbom.json
uv run --with pip-audit pip-audit --strict
```

CI runs both on every push. Regenerate and re-read this document whenever the
lockfile changes materially — a new transitive dependency can introduce an
obligation that nothing in the diff makes obvious.
