#!/usr/bin/env bash
# Refresh the pinned base-image digests (SVX-TECH-001 section 2.3).
#
# Pinning by digest makes builds reproducible, but a pinned image stops
# receiving security patches — the two properties are in tension, and the
# resolution is to re-pin deliberately rather than to float the tag.
#
# Run this on the monthly dependency review, and immediately when a base image
# has a published critical advisory. It prints what changed; it does not
# commit, because a base-image bump is a change that goes through review with
# the regression suite like any other.
#
#   ./infra/refresh-digests.sh            # report drift
#   ./infra/refresh-digests.sh --write    # rewrite the files

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WRITE=false
[[ "${1:-}" == "--write" ]] && WRITE=true

TODAY="$(date -u +%Y-%m-%d)"
drift=0

# image_ref : file : tag
TARGETS=(
  "library/python:3.13-slim-bookworm:${SCRIPT_DIR}/Dockerfile.api:python"
  "library/postgres:17-alpine:${SCRIPT_DIR}/docker-compose.yml:postgres"
  "library/caddy:2-alpine:${SCRIPT_DIR}/docker-compose.yml:caddy"
)

resolve() {
  local repo="$1" tag="$2" token
  token=$(curl -fsSL \
    "https://auth.docker.io/token?service=registry.docker.io&scope=repository:${repo}:pull" \
    | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
  curl -fsSI \
    -H "Authorization: Bearer ${token}" \
    -H "Accept: application/vnd.oci.image.index.v1+json" \
    -H "Accept: application/vnd.docker.distribution.manifest.list.v2+json" \
    -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
    "https://registry-1.docker.io/v2/${repo}/manifests/${tag}" \
    | tr -d '\r' | sed -n 's/^[Dd]ocker-[Cc]ontent-[Dd]igest: //p'
}

for entry in "${TARGETS[@]}"; do
  repo="${entry%%:*}"
  rest="${entry#*:}"
  tag="${rest%%:*}"
  rest="${rest#*:}"
  file="${rest%:*}"
  short="${rest##*:}"

  echo "checking ${repo}:${tag}"
  latest="$(resolve "$repo" "$tag")"
  if [[ -z "$latest" ]]; then
    echo "  could not resolve a digest; leaving the pin alone" >&2
    continue
  fi

  current="$(grep -oE "${short}@sha256:[0-9a-f]{64}" "$file" | head -1 || true)"
  current="${current#*@}"

  if [[ "$current" == "$latest" ]]; then
    echo "  up to date (${latest:0:19}...)"
    continue
  fi

  drift=$((drift + 1))
  echo "  DRIFT"
  echo "    pinned: ${current:-none}"
  echo "    latest: ${latest}"

  if $WRITE; then
    # Replace the digest and refresh the dated comment beside it.
    sed -i -E \
      -e "s|${short}@sha256:[0-9a-f]{64}|${short}@${latest}|g" \
      -e "s|(${repo##*/}:${tag//./\\.}[^0-9]*)[0-9]{4}-[0-9]{2}-[0-9]{2}|\1${TODAY}|g" \
      "$file"
    echo "    updated ${file##*/}"
  fi
done

echo
if (( drift == 0 )); then
  echo "All base images are current."
elif $WRITE; then
  echo "${drift} image(s) re-pinned. Run the full test suite, then review the diff."
  echo "A base-image bump is a reviewed change, not a housekeeping commit."
else
  echo "${drift} image(s) have drifted. Re-run with --write to update them."
  exit 1
fi
