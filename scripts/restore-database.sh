#!/usr/bin/env bash
#
# Restore the archive database from a dump.
#
#   ./scripts/restore-database.sh backups/formula1-dashboard-<stamp>.dump
#
# This replaces the contents of the target database. It refuses to run unless
# the target is named explicitly, because a restore that goes to the wrong
# place is indistinguishable from data loss.
#
# Use RESTORE_DB to send it somewhere other than the live database — which is
# how a restore should be rehearsed. A backup nobody has restored is a
# hypothesis, not a backup:
#
#   RESTORE_DB=restore_check ./scripts/restore-database.sh <dump>
#
# Environment:
#   COMPOSE_FILE      compose file to use   (default: compose.yaml)
#   POSTGRES_SERVICE  service name          (default: db)
#   POSTGRES_USER     role                  (default: formula1_dashboard)
#   POSTGRES_PASSWORD required
#   RESTORE_DB        target database       (default: formula1_dashboard)
#   ASSUME_YES        skip the prompt when restoring over the live database
#
set -euo pipefail

DUMP="${1:-}"
COMPOSE_FILE="${COMPOSE_FILE:-compose.yaml}"
SERVICE="${POSTGRES_SERVICE:-db}"
USER_NAME="${POSTGRES_USER:-formula1_dashboard}"
LIVE_DB="${POSTGRES_DB:-formula1_dashboard}"
TARGET_DB="${RESTORE_DB:-$LIVE_DB}"
PASSWORD="${POSTGRES_PASSWORD:-}"

if [ -z "$DUMP" ]; then
  echo "Usage: $0 <dump file>" >&2
  exit 1
fi
if [ ! -f "$DUMP" ]; then
  echo "No such dump: $DUMP" >&2
  exit 1
fi
if [ -z "$PASSWORD" ]; then
  echo "POSTGRES_PASSWORD must be set." >&2
  exit 1
fi

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }
psql_run() {
  compose exec -T -e PGPASSWORD="$PASSWORD" "$SERVICE" \
    psql -w -v ON_ERROR_STOP=1 -U "$USER_NAME" "$@"
}

if ! compose ps --status running --services 2>/dev/null | grep -qx "$SERVICE"; then
  echo "The '$SERVICE' service is not running." >&2
  exit 1
fi

if [ "$TARGET_DB" = "$LIVE_DB" ] && [ "${ASSUME_YES:-}" != "1" ]; then
  echo "This will REPLACE the contents of the live database '$LIVE_DB'."
  echo "To rehearse instead, re-run with RESTORE_DB=restore_check."
  printf "Type the database name to continue: "
  read -r reply
  if [ "$reply" != "$LIVE_DB" ]; then
    echo "Cancelled." >&2
    exit 1
  fi
fi

echo "==> Stopping the application so nothing writes during the restore"
compose stop api worker >/dev/null 2>&1 || true

echo "==> Preparing '$TARGET_DB'"
if [ "$TARGET_DB" != "$LIVE_DB" ]; then
  psql_run -d postgres -qtAc "DROP DATABASE IF EXISTS \"$TARGET_DB\";" >/dev/null
  psql_run -d postgres -qtAc "CREATE DATABASE \"$TARGET_DB\";" >/dev/null
fi

echo "==> Restoring"
# --clean --if-exists drops each object before recreating it, so restoring over
# an existing database replaces it rather than colliding with it.
compose exec -T -e PGPASSWORD="$PASSWORD" "$SERVICE" \
  pg_restore -U "$USER_NAME" -d "$TARGET_DB" --clean --if-exists --no-owner \
  --exit-on-error < "$DUMP"

echo "==> Checking what landed"
psql_run -d "$TARGET_DB" -qtAc "
  SELECT 'sessions=' || (SELECT count(*) FROM sessions)
      || ' events=' || (SELECT count(*) FROM events)
      || ' laps=' || (SELECT count(*) FROM laps)
      || ' results=' || (SELECT count(*) FROM session_results);
"

if [ "$TARGET_DB" = "$LIVE_DB" ]; then
  echo "==> Starting the application"
  compose up -d api worker >/dev/null
  echo
  echo "Restored into the live database."
else
  echo
  echo "Restored into '$TARGET_DB', leaving the live database untouched."
  echo "Drop the rehearsal copy when you are done:"
  echo "  docker compose -f $COMPOSE_FILE exec -e PGPASSWORD=... $SERVICE \\"
  echo "    psql -U $USER_NAME -d postgres -c 'DROP DATABASE \"$TARGET_DB\";'"
  echo
  echo "The application was stopped; start it again with:"
  echo "  docker compose -f $COMPOSE_FILE up -d api worker"
fi
