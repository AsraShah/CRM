#!/usr/bin/env bash
# Restore into an ISOLATED environment (SVX-TECH-001 section 12.2, RD09).
#
# This is the script that turns "we take backups" into "we can recover". Run it
# before launch and monthly thereafter, against a clean environment, and record
# the evidence.
#
#   ./restore.sh --snapshot latest --target-db svx_restore_check
#
# It refuses to run against the production database. Restoring over live data is
# a data-loss decision made by a person, not a default (section 12.3).

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SNAPSHOT="latest"
TARGET_DB=""
STAGING="$(mktemp -d)"
START_EPOCH="$(date -u +%s)"

log() { printf '%s restore: %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }
cleanup() { rm -rf "$STAGING"; }
trap cleanup EXIT
trap 'log "FAILED at line $LINENO"; exit 1' ERR

usage() {
  cat >&2 <<'USAGE'
Usage: restore.sh --target-db NAME [--snapshot ID] [--env FILE]

  --target-db  Database to restore INTO. Must not be the production database.
  --snapshot   restic snapshot id, or "latest" (default).
  --env        Environment file (default: ../.env).
USAGE
  exit 2
}

ENV_FILE="${SCRIPT_DIR}/../.env"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --snapshot)  SNAPSHOT="$2"; shift 2 ;;
    --target-db) TARGET_DB="$2"; shift 2 ;;
    --env)       ENV_FILE="$2"; shift 2 ;;
    -h|--help)   usage ;;
    *)           log "Unknown argument: $1"; usage ;;
  esac
done

[[ -n "$TARGET_DB" ]] || usage
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

: "${RESTIC_REPOSITORY:?}"
: "${RESTIC_PASSWORD:?}"
: "${POSTGRES_DB:=svx}"

# The guard that matters. A restore drill must never be one typo away from
# destroying the thing it is meant to protect.
if [[ "$TARGET_DB" == "$POSTGRES_DB" ]]; then
  log "REFUSING: --target-db is the production database (${POSTGRES_DB})."
  log "A production restore is a deliberate data-loss decision. See"
  log "docs/runbooks/recovery.md for that procedure."
  exit 1
fi

log "restoring snapshot ${SNAPSHOT} into ${TARGET_DB}"

# ---------------------------------------------------------------------------
# 1. Retrieve
# ---------------------------------------------------------------------------
restic restore "$SNAPSHOT" --target "$STAGING"

DUMP="$(find "$STAGING" -name 'database-*.dump' | sort | tail -1)"
[[ -n "$DUMP" ]] || { log "No database dump in that snapshot."; exit 1; }
log "found $(basename "$DUMP") ($(stat -c%s "$DUMP") bytes)"

# ---------------------------------------------------------------------------
# 2. Restore
# ---------------------------------------------------------------------------
log "creating ${TARGET_DB}"
psql -v ON_ERROR_STOP=1 -U "${SVX_OWNER_USER:-svx_owner}" -d postgres \
  -c "DROP DATABASE IF EXISTS ${TARGET_DB};" \
  -c "CREATE DATABASE ${TARGET_DB};"

log "loading dump"
pg_restore \
  --username="${SVX_OWNER_USER:-svx_owner}" \
  --dbname="$TARGET_DB" \
  --no-owner \
  --exit-on-error \
  "$DUMP"

# ---------------------------------------------------------------------------
# 3. Reconcile
# ---------------------------------------------------------------------------
# Counts are the cheap check. They catch a truncated dump immediately; they do
# not prove the data is coherent, which is what the journey test below is for.
log "record counts in the restored database:"
psql -U "${SVX_OWNER_USER:-svx_owner}" -d "$TARGET_DB" <<'SQL'
SELECT 'workspaces'    AS entity, count(*) FROM identity_workspace
UNION ALL SELECT 'memberships',   count(*) FROM identity_membership
UNION ALL SELECT 'contacts',      count(*) FROM crm_contact
UNION ALL SELECT 'leads',         count(*) FROM crm_lead
UNION ALL SELECT 'opportunities', count(*) FROM crm_opportunity
UNION ALL SELECT 'activities',    count(*) FROM crm_activity
UNION ALL SELECT 'tasks',         count(*) FROM work_task
UNION ALL SELECT 'audit events',  count(*) FROM common_audit_event
ORDER BY entity;
SQL

# Row-level security must survive the restore. A dump/restore that quietly drops
# the policies would leave a database that looks complete and isolates nothing.
log "verifying row-level security survived the restore:"
psql -U "${SVX_OWNER_USER:-svx_owner}" -d "$TARGET_DB" -tAc "
  SELECT count(*) FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'public'
     AND c.relname LIKE ANY (ARRAY['crm_%','work_%','automation_%','common_%'])
     AND c.relkind = 'r'
     AND (c.relrowsecurity IS FALSE OR c.relforcerowsecurity IS FALSE);
" | {
  read -r unprotected
  if [[ "$unprotected" != "0" ]]; then
    log "WARNING: ${unprotected} tenant table(s) lack forced RLS after restore."
    log "Re-run the RLS migration against the restored database before use."
  else
    log "all tenant tables still have forced row-level security"
  fi
}

ELAPSED=$(( $(date -u +%s) - START_EPOCH ))
log "restore completed in ${ELAPSED}s (RTO target: 4 hours, staffed)"

cat >&2 <<'NEXT'

The restore is not yet verified. To complete the drill:

  1. Point an application instance at the restored database.
  2. Exercise one full lead-to-client journey: sign in, open Today, complete a
     follow-up, move a deal's stage.
  3. Confirm access configuration: a suspended member is still suspended, and a
     representative still cannot see a colleague's records.
  4. Record the date, snapshot id, elapsed time and outcome in the recovery log.

A restore that loads without error but was never exercised is not evidence.
NEXT
