# Backup and recovery

**Reference:** SVX-TECH-001 section 12.2; RD09; CRM13

**Targets:** RPO at most 24 hours. RTO within 4 hours, **staffed**.

Both are internal engineering targets measured under a staffed test. Neither is
a 24-hour support promise, and neither may be presented to a customer as an SLA
(ADR006).

---

## What is backed up

| What | How | Where |
| --- | --- | --- |
| Database | `pg_dump --format=custom` as the owner role | restic snapshot |
| Private files | tar of the `private-files` volume | restic snapshot |
| Configuration | `docker-compose.yml`, `Caddyfile`, non-secret `.env` keys | restic snapshot |
| Whole-VM state | Weekly provider snapshot | Provider |

**Secrets are not in the backup.** `DJANGO_SECRET_KEY`, database passwords, the
credential encryption key and the restic password live in a company-controlled
vault, separate from the server. A backup that carries its own decryption key
widens the blast radius of a compromised backup store rather than reducing it.

**A backup without a recoverable key is unusable.** Verify vault access as part
of the monthly drill, not at the moment you need it.

The weekly VM snapshot **supplements** logical backups; it does not replace
them. A VM snapshot cannot restore a single dropped table, and it captures the
database mid-write.

---

## Schedule

```
# Nightly at 02:30 in the workspace time zone.
30 2 * * *  cd /srv/scalevexo && ./infra/backup.sh >> /var/log/svx-backup.log 2>&1
```

Monitoring watches **backup age**, not just the exit code (section 12.1). A cron
job that stopped being scheduled produces no failures at all, which is
indistinguishable from success if you only watch for errors.

---

## Monthly restore drill

Run before launch and monthly thereafter. Budget one hour.

```bash
cd /srv/scalevexo
./infra/restore.sh --snapshot latest --target-db svx_restore_check
```

The script refuses to target the production database. It restores, prints
record counts, and verifies that row-level security survived the restore — a
dump that reloads without its policies looks complete and isolates nothing.

Then complete the drill by hand:

1. Point a spare application instance at the restored database.
2. Exercise one full journey: sign in, open Today, complete a follow-up with a
   next action, move a deal's stage.
3. Verify access configuration: a suspended member is still suspended; a
   representative still cannot reach a colleague's records.
4. Record in the recovery log: date, operator, snapshot id, elapsed time,
   record counts, anything that went wrong.

**A successful upload is not a restore test.** A restore that loads without
error but was never exercised is not evidence either. Step 2 is the test.

---

## Real recovery

### Database corruption or accidental deletion

1. **Stop writes first.** `docker compose stop api worker`. Every minute of
   continued writing is a minute of work that the restore will discard.
2. Identify the last good snapshot: `restic snapshots --tag scalevexo`.
3. Restore to a **new** database name and verify it (as in the drill).
4. Only once verified, and with an explicit decision recorded, repoint the
   application.

Restoring over live data destroys everything written since the snapshot. It is
a data-loss decision made by a named person, not a default rollback step
(section 12.3).

### Host loss

1. Provision a replacement host from the same image and region.
2. Restore configuration from the snapshot; retrieve secrets from the vault.
3. Recreate the database roles (`infra/postgres/00-roles.sh`) — they are roles,
   not data, so they are not in the dump.
4. Restore the database and files.
5. Run `manage.py migrate --database=owner` only if the code is newer than the
   backup. Running migrations against a *newer* backup than the code will fail;
   deploy the matching code first.
6. Smoke-test: login, an access boundary, a write, rule processing, health
   checks.
7. Update DNS. Caddy obtains a fresh certificate automatically.

### Failed deployment

The prior image is retained. Roll back to it.

Database restoration is **not** the default rollback method once new writes have
landed (section 12.3). Schema changes use expand-and-contract precisely so that
the previous image still runs against the new schema.

---

## After any recovery

- Reconcile record counts against the last known-good report.
- Check the job queue: work leased by the lost worker is reclaimed
  automatically once its lease expires, but jobs in the failed queue need an
  operator decision.
- For anything that touched an external provider, reconcile against provider
  state. Never blindly replay a send after an ambiguous outcome.
- Tell the affected users what was lost and what was recovered. Do not announce
  containment before evidence confirms it (section 12.3).
