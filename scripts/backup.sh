#!/bin/sh
# Encrypted MySQL dump with retention.
#
# The archive never contains the application encryption keys or the .env file:
# they must be stored separately, otherwise a single leaked backup would expose
# both the ciphertext and the key.
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
PASSPHRASE_FILE="${BACKUP_PASSPHRASE_FILE:-/run/secrets/backup_passphrase}"
PASSPHRASE_SOURCE=""
DATABASE="${MYSQL_DATABASE:-opbg_bot}"
HOST="${DATABASE_HOST:-mysql}"
PORT="${DATABASE_PORT:-3306}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SCHEMA_VERSION="${SCHEMA_VERSION:-unknown}"
ARTIFACT="${BACKUP_DIR}/opbg-${TIMESTAMP}-${SCHEMA_VERSION}.sql.gz.enc"

# The passphrase may come from a mounted secret file or, for a one-off run,
# from the environment. It is never written next to the archive.
if [ -r "${PASSPHRASE_FILE}" ]; then
  PASSPHRASE_SOURCE="file:${PASSPHRASE_FILE}"
elif [ -n "${BACKUP_PASSPHRASE:-}" ]; then
  PASSPHRASE_SOURCE="env:BACKUP_PASSPHRASE"
else
  echo "no backup passphrase: mount ${PASSPHRASE_FILE} or set BACKUP_PASSPHRASE" >&2
  exit 2
fi

mkdir -p "${BACKUP_DIR}"
umask 077

mysqldump \
  --host="${HOST}" \
  --port="${PORT}" \
  --user=root \
  --password="${MYSQL_ROOT_PASSWORD}" \
  --single-transaction \
  --quick \
  --routines \
  --events \
  --default-character-set=utf8mb4 \
  "${DATABASE}" \
  | gzip -9 \
  | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass "${PASSPHRASE_SOURCE}" \
  > "${ARTIFACT}"

SIZE="$(wc -c < "${ARTIFACT}" | tr -d ' ')"
echo "backup written: ${ARTIFACT} (${SIZE} bytes)"

find "${BACKUP_DIR}" -name 'opbg-*.sql.gz.enc' -type f -mtime "+${RETENTION_DAYS}" -delete

python -m app.cli record-backup --outcome SUCCESS --artifact "$(basename "${ARTIFACT}")" --size "${SIZE}" || true
