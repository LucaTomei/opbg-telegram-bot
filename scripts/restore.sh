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
if [ -r "${PASSPHRASE_FILE}" ]; then
  PASSPHRASE_SOURCE="file:${PASSPHRASE_FILE}"
elif [ -n "${BACKUP_PASSPHRASE:-}" ]; then
  PASSPHRASE_SOURCE="env:BACKUP_PASSPHRASE"
else
  echo "no backup passphrase: mount ${PASSPHRASE_FILE} or set BACKUP_PASSPHRASE" >&2
  exit 2
fi

# The password travels through MYSQL_PWD instead of the command line.
export MYSQL_PWD="${MYSQL_ROOT_PASSWORD}"

mysql --host="${HOST}" --port="${PORT}" --user=root \
  --execute="CREATE DATABASE IF NOT EXISTS \`${TARGET}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"

openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "${PASSPHRASE_SOURCE}" -in "${ARTIFACT}" \
  | gunzip \
  | mysql --host="${HOST}" --port="${PORT}" --user=root "${TARGET}"

echo "restore completed into database ${TARGET}"
echo "verify it, then point DATABASE settings to it only after a manual check"
