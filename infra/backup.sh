#!/usr/bin/env bash
# Nightly encrypted backup (SVX-TECH-001 section 12.2).
#
#   pg_dump (custom format) + private files + configuration
#     -> restic snapshot -> separate storage account
#
# Two things this script insists on:
#
# 1. A successful upload is not a restore test. It verifies integrity with
#    `restic check` and records the snapshot, but only infra/restore.sh run
#    against a clean environment proves the backup is usable.
# 2. The encryption key lives in a company-controlled vault, separate from this
#    server. A backup whose key is only on the host it protects is not a backup.
#
# Retention target: 7 daily, 4 weekly. RPO 24 hours; staffed RTO 4 hours.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/../.env}"
STAGING="$(mktemp -d)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

log() { printf '%s backup: %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }

cleanup() {
  # The dump holds every customer record in clear text; it must not outlive
  # this run on local disk.
  rm -rf "$STAGING"
}
trap cleanup EXIT

on_error() {
  log "FAILED at line $1. The snapshot was not completed."
  exit 1
}
trap 'on_error $LINENO' ERR

[[ -f "$ENV_FILE" ]] || { log "No env file at $ENV_FILE"; exit 1; }
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY must be set}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD must be set}"
: "${POSTGRES_DB:=svx}"

log "starting ${STAMP}"

# ---------------------------------------------------------------------------
# 1. Database
# ---------------------------------------------------------------------------
# Custom format so a restore can be selective and parallel. Dumped as the owner
# role: the application role cannot read every table by design.
log "dumping database"
docker compose -f "${SCRIPT_DIR}/docker-compose.yml" exec -T db \
  pg_dump \
    --username="${SVX_OWNER_USER:-svx_owner}" \
    --dbname="${POSTGRES_DB}" \
    --format=custom \
    --compress=9 \
    --no-owner \
  > "${STAGING}/database-${STAMP}.dump"

DUMP_BYTES=$(stat -c%s "${STAGING}/database-${STAMP}.dump")
# An empty or near-empty dump means pg_dump failed quietly; better to fail the
# backup loudly than to upload something unusable and call it a success.
if (( DUMP_BYTES < 4096 )); then
  log "dump is only ${DUMP_BYTES} bytes; refusing to upload"
  exit 1
fi
log "dump is ${DUMP_BYTES} bytes"

# ---------------------------------------------------------------------------
# 2. Private files and configuration
# ---------------------------------------------------------------------------
log "collecting private files"
docker run --rm \
  -v scalevexo_private-files:/data:ro \
  -v "${STAGING}:/out" \
  alpine:3 tar czf "/out/private-files-${STAMP}.tar.gz" -C /data .

# The configuration needed to rebuild the host, minus the secrets themselves:
# those live in the vault, and a backup that carries them widens the blast
# radius of a compromised backup store.
grep -vE '^(DJANGO_SECRET_KEY|.*PASSWORD|.*_KEY|OPENAI_API_KEY|RESTIC_PASSWORD)=' \
  "$ENV_FILE" > "${STAGING}/config-${STAMP}.env" || true
cp "${SCRIPT_DIR}/docker-compose.yml" "${SCRIPT_DIR}/Caddyfile" "${STAGING}/"

# ---------------------------------------------------------------------------
# 3. Encrypted off-server snapshot
# ---------------------------------------------------------------------------
log "uploading snapshot"
restic backup "${STAGING}" \
  --tag "scalevexo" \
  --tag "automated" \
  --host "${BACKUP_HOST_LABEL:-scalevexo-pilot}"

log "verifying repository integrity"
restic check --read-data-subset=5%

log "applying retention"
restic forget \
  --keep-daily 7 \
  --keep-weekly 4 \
  --tag "scalevexo" \
  --prune

# ---------------------------------------------------------------------------
# 4. Evidence
# ---------------------------------------------------------------------------
# Backup age is monitored (section 12.1). A monitor that only watches the
# process exit code cannot tell a stalled schedule from a healthy one.
restic snapshots --latest 1 --json > "${SCRIPT_DIR}/last-backup.json"

log "complete: ${STAMP}"
log "REMINDER: a successful upload is not a restore test. Run infra/restore.sh"
log "into an isolated environment monthly, per docs/runbooks/recovery.md."
