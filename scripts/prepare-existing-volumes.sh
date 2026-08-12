#!/usr/bin/env bash
#
# Hand the application's data volumes to the unprivileged container user.
#
# The API and worker images run as uid 10001 rather than root. Docker seeds an
# empty named volume from the image directory it covers, ownership included, so
# a fresh installation needs nothing. A volume that already holds data is left
# untouched by Docker and keeps its original root ownership, which the
# unprivileged user cannot write to — the FastF1 cache, the disposable session
# logs, and the stored F1 TV token all live in such volumes.
#
# Run this once when upgrading an installation that predates the non-root
# images. It changes ownership only; no data is read, moved, or deleted.
#
#   ./scripts/prepare-existing-volumes.sh
#
set -euo pipefail

UID_GID="${APP_UID_GID:-10001:10001}"
PROJECT="${COMPOSE_PROJECT_NAME:-formula1-dashboard}"
VOLUMES=(
  "${PROJECT}_fastf1_cache"
  "${PROJECT}_live_sessions"
  "${PROJECT}_live_auth"
)

if docker compose ps --status running --services 2>/dev/null | grep -qE '^(api|worker)$'; then
  echo "Stop the application first, so nothing is writing while ownership changes:" >&2
  echo "  docker compose down" >&2
  exit 1
fi

changed=0
for volume in "${VOLUMES[@]}"; do
  if ! docker volume inspect "$volume" >/dev/null 2>&1; then
    echo "==> $volume does not exist yet; nothing to do"
    continue
  fi

  owner="$(docker run --rm -v "$volume":/target alpine stat -c '%u:%g' /target)"
  if [ "$owner" = "$UID_GID" ]; then
    echo "==> $volume already owned by $UID_GID"
    continue
  fi

  echo "==> $volume: $owner -> $UID_GID"
  docker run --rm -v "$volume":/target alpine chown -R "$UID_GID" /target
  changed=$((changed + 1))
done

echo
if [ "$changed" -eq 0 ]; then
  echo "Nothing needed changing."
else
  echo "Updated $changed volume(s). Start the application again:"
  echo "  docker compose up -d"
fi
