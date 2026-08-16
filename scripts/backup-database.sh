#!/usr/bin/env bash
#
# Take a compressed, restorable dump of the archive database.
#
#   ./scripts/backup-database.sh
#
# Writes backups/formula1-dashboard-<UTC timestamp>.dump and keeps the most
# recent BACKUP_KEEP of them. The custom format (-Fc) is used rather than plain
# SQL because it is already compressed, restores in parallel, and lets a single
# table be pulled out of a dump without replaying the whole thing.
#
# The dump is verified before it is kept: pg_restore --list must be able to
# read its table of contents. A truncated dump that nobody notices is worse
# than no dump, because it is trusted.
#
# Off-site copying is deliberately not implemented here, because guessing a
# storage provider would mean shipping something untestable. Set
# BACKUP_UPLOAD_COMMAND to a command receiving the dump path instead:
#
#   BACKUP_UPLOAD_COMMAND='rclone copy {} r2:f1-backups/'
#   BACKUP_UPLOAD_COMMAND='aws s3 cp {} s3://f1-backups/'
#
# A backup that never leaves the machine it protects is not an off-site backup.
#
# Environment:
#   COMPOSE_FILE            compose file to use  (default: compose.yaml)
#   POSTGRES_SERVICE        service name         (default: db)
#   POSTGRES_USER/DB        role and database    (default: formula1_dashboard)
#   POSTGRES_PASSWORD       required
#   BACKUP_DIR              output directory     (default: ./backups)
#   BACKUP_KEEP             dumps to retain      (default: 14)
#   BACKUP_UPLOAD_COMMAND   optional; "{}" is replaced with the dump path
#
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-compose.yaml}"
SERVICE="${POSTGRES_SERVICE:-db}"
USER_NAME="${POSTGRES_USER:-formula1_dashboard}"
DB_NAME="${POSTGRES_DB:-formula1_dashboard}"
PASSWORD="${POSTGRES_PASSWORD:-}"
BACKUP_DIR="${BACKUP_DIR:-backups}"
KEEP="${BACKUP_KEEP:-14}"

if [ -z "$PASSWORD" ]; then
  echo "POSTGRES_PASSWORD must be set." >&2
  exit 1
fi

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

if ! compose ps --status running --services 2>/dev/null | grep -qx "$SERVICE"; then
  echo "The '$SERVICE' service is not running." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="$BACKUP_DIR/formula1-dashboard-$STAMP.dump"
PARTIAL="$TARGET.partial"

# Written to .partial first and renamed only once verified, so an interrupted
# run never leaves something that looks like a usable backup.
echo "==> Dumping $DB_NAME"
compose exec -T -e PGPASSWORD="$PASSWORD" "$SERVICE" \
  pg_dump -U "$USER_NAME" -d "$DB_NAME" --format=custom --compress=6 \
  > "$PARTIAL"

SIZE="$(wc -c < "$PARTIAL" | tr -d ' ')"
if [ "$SIZE" -lt 1024 ]; then
  echo "Dump is only ${SIZE} bytes; refusing to keep it." >&2
  rm -f "$PARTIAL"
  exit 1
fi

echo "==> Verifying the dump is readable"
TABLES="$(compose exec -T "$SERVICE" pg_restore --list < "$PARTIAL" | grep -c 'TABLE DATA' || true)"
if [ "${TABLES:-0}" -lt 1 ]; then
  echo "The dump contains no table data; refusing to keep it." >&2
  rm -f "$PARTIAL"
  exit 1
fi

mv "$PARTIAL" "$TARGET"
echo "    $TARGET ($(( SIZE / 1024 )) KiB, $TABLES tables)"

if [ -n "${BACKUP_UPLOAD_COMMAND:-}" ]; then
  echo "==> Copying off-site"
  # shellcheck disable=SC2001
  UPLOAD="$(echo "$BACKUP_UPLOAD_COMMAND" | sed "s|{}|$TARGET|g")"
  if sh -c "$UPLOAD"; then
    echo "    uploaded"
  else
    # The local dump is already good, so a failed upload must not discard it —
    # but it must not pass silently either.
    echo "FAILED: the off-site copy did not succeed. The local dump was kept." >&2
    exit 1
  fi
else
  echo "==> No BACKUP_UPLOAD_COMMAND set; this dump exists only on this host"
fi

echo "==> Pruning to the most recent $KEEP"
# shellcheck disable=SC2012
ls -1t "$BACKUP_DIR"/formula1-dashboard-*.dump 2>/dev/null \
  | tail -n +$((KEEP + 1)) \
  | while read -r old; do
      echo "    removing $(basename "$old")"
      rm -f "$old"
    done

echo
echo "Done. Restore with:"
echo "  ./scripts/restore-database.sh $TARGET"
