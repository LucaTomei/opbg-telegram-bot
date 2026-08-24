#!/bin/sh
# Restore an encrypted backup into a temporary database.
#
# The restore always targets a separate database name so a production schema is
# never overwritten by accident.
set -eu

ARTIFACT="${1:?usage: restore.sh <artifact> [target-database]}"
TARGET="${2:-opbg_bot_restore}"
PASSPHRASE_FILE="${BACKUP_PASSPHRASE_FILE:-/run/secrets/backup_passphrase}"
HOST="${DATABASE_HOST:-mysql}"
PORT="${DATABASE_PORT:-3306}"

if [ ! -r "${ARTIFACT}" ]; then
  echo "artifact not readable: ${ARTIFACT}" >&2
  exit 2
fi
if [ ! -r "${PASSPHRASE_FILE}" ]; then
  echo "backup passphrase file is missing: ${PASSPHRASE_FILE}" >&2
  exit 2
fi

mysql --host="${HOST}" --port="${PORT}" --user=root --password="${MYSQL_ROOT_PASSWORD}" \
  --execute="CREATE DATABASE IF NOT EXISTS \`${TARGET}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"

openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "file:${PASSPHRASE_FILE}" -in "${ARTIFACT}" \
  | gunzip \
  | mysql --host="${HOST}" --port="${PORT}" --user=root --password="${MYSQL_ROOT_PASSWORD}" "${TARGET}"

echo "restore completed into database ${TARGET}"
echo "verify it, then point DATABASE settings to it only after a manual check"
