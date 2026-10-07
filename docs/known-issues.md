# Known-issues register

Required handover artifact (SVX-TECH-001 section 14). Open items that are known
and not yet resolved, as of 2026-10-06. Each names the requirement it affects.

## Release-gate evidence not yet produced

| ID | Item | Requirement | Note |
| --- | --- | --- | --- |
| KI-01 | RD03 load and 24-hour soak not run | Section 11.2, G1 | Fixture and k6 script ready in `infra/load/`; must run on the 2 GiB reference host |
| KI-02 | RD09 restore drill not performed | CRM13, G1 | `infra/restore.sh` exists; no recorded restore. Live data must not be used before it passes |
| KI-03 | RD08 AI evaluation (50 cases) not run | CRM12 | AI ships disabled; the review screen appears only if it is enabled |
| KI-04 | RD05 adversarial isolation testing not run | CRM01, ADR007 | Automated isolation and composite-key tests pass; adversarial testing is separate |
| KI-05 | RD04 worker crash testing, RD06 migration with real data, RD07 calendar edge cases not recorded as experiments | Section 17 | Unit tests cover the mechanisms; the experiments need recorded runs |
| KI-06 | OWASP ASVS L2 review and manual keyboard / screen-reader checks not performed | Sections 5.4, 7.3 | Automated axe checks pass on core screens in the e2e journey |
| KI-07 | Supported browsers other than Chrome not tested | Section 7.3 | Edge, Firefox and Safari are targets |
| KI-08 | No deployment; CI not yet run on Linux | RD02 | The Linux half of RD02 is unconfirmed until CI runs green |

## Product gaps (accepted for Release 1, or later releases)

| ID | Item | Requirement |
| --- | --- | --- |
| KI-10 | No email or calling connector; all activity is self-reported | CRM04, Release 2 |
| KI-11 | Approved leave does not yet influence notification schedules | CRM05 ("once configured") |
| KI-12 | Rule editing is through template forms only; conditions are not editable in the interface (they are through the API) | CRM06 |
| KI-13 | Simulation takes a record identifier rather than offering a picker | CRM06 |
| KI-14 | Deal and lead records open in a dialog; there is no standalone deal page (the client record shows a won deal's history) | Screen acceptance |
| KI-15 | Attendance, leave routing, client portal, billing: later releases | Releases 2–3 |

## Operational notes

- Windows developer machines with Smart App Control block the virtualenv's
  `python.exe` and psycopg's compiled driver. Workaround: run the base Python
  3.13 with the venv's site-packages on `PYTHONPATH` and `PSYCOPG_IMPL=python`.
  Linux and CI are unaffected.
- `seed_e2e_workspace` and `seed_load_fixture` create users with a known
  password and refuse to run with `DEBUG` off. Never run them in production.
