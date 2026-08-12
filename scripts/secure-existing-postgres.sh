#!/usr/bin/env bash
#
# Require a password on a PostgreSQL volume that was initialised with trust.
#
# POSTGRES_PASSWORD only takes effect when a cluster is first created, so a
# volume that already exists keeps the pg_hba.conf it was initialised with —
# "host all all all trust", which accepts any connection without a password.
# Changing compose.yaml alone therefore secures new deployments and leaves
# existing ones open. This closes that gap in place, without touching data.
#
# Idempotent: safe to run more than once.
#
#   POSTGRES_PASSWORD=... ./scripts/secure-existing-postgres.sh
#
set -euo pipefail

SERVICE="${POSTGRES_SERVICE:-db}"
USER_NAME="${POSTGRES_USER:-formula1_dashboard}"
PASSWORD="${POSTGRES_PASSWORD:-}"

if [ -z "$PASSWORD" ]; then
  echo "POSTGRES_PASSWORD must be set." >&2
  echo "It must match the value compose.yaml passes in DATABASE_URL." >&2
  exit 1
fi

if ! docker compose ps --status running --services | grep -qx "$SERVICE"; then
  echo "The '$SERVICE' service is not running. Start it first:" >&2
  echo "  docker compose up -d $SERVICE" >&2
  exit 1
fi

echo "==> Setting the password for role '$USER_NAME'"
# Passed as a parameter rather than interpolated into SQL, so a password
# containing quotes cannot break out of the statement.
# Fed on stdin rather than with -c, because psql does not interpolate
# variables into a -c string.
printf "ALTER ROLE %s PASSWORD :'pw';\n" "$USER_NAME" \
  | docker compose exec -T -e PGPASSWORD="$PASSWORD" "$SERVICE" \
    psql -v ON_ERROR_STOP=1 -U "$USER_NAME" -d postgres -q -v pw="$PASSWORD"

echo "==> Requiring scram-sha-256 for host connections"
docker compose exec -T "$SERVICE" sh -c '
  set -e
  HBA=/var/lib/postgresql/data/pg_hba.conf
  if ! grep -qE "^\s*(host|local)[^#]*\btrust\b" "$HBA"; then
    echo "    already password-protected"
    exit 0
  fi
  cp "$HBA" "$HBA.trust.bak"
  # Local socket connections keep peer auth; every host line requires scram.
  sed -i -E "s/^([[:space:]]*host[^#]*)\btrust\b/\1scram-sha-256/" "$HBA"
  sed -i -E "s/^([[:space:]]*local[^#]*)\btrust\b/\1scram-sha-256/" "$HBA"
  echo "    rewrote pg_hba.conf (previous copy kept at pg_hba.conf.trust.bak)"
'

echo "==> Reloading the server"
docker compose exec -T -e PGPASSWORD="$PASSWORD" "$SERVICE" \
  psql -v ON_ERROR_STOP=1 -U "$USER_NAME" -d postgres -qtAc "SELECT pg_reload_conf();" >/dev/null

echo "==> Verifying that a passwordless connection is now refused"
if docker compose exec -T -e PGPASSWORD= "$SERVICE" \
  psql -w -U "$USER_NAME" -d postgres -h 127.0.0.1 -qtAc "SELECT 1" >/dev/null 2>&1; then
  echo "FAILED: the server still accepts connections without a password." >&2
  exit 1
fi
echo "    refused, as intended"

echo "==> Verifying that the password is accepted"
docker compose exec -T -e PGPASSWORD="$PASSWORD" "$SERVICE" \
  psql -w -v ON_ERROR_STOP=1 -U "$USER_NAME" -d postgres -h 127.0.0.1 -qtAc "SELECT 1" >/dev/null
echo "    accepted"

echo
echo "Done. Restart the application so it reconnects with the password:"
echo "  docker compose up -d --force-recreate api worker"
