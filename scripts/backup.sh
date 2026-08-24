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

# The password travels through MYSQL_PWD instead of the command line, so it
# never shows up in the process list.
export MYSQL_PWD="${MYSQL_ROOT_PASSWORD}"

# The archive is named after the revision actually applied, not after a guess.
SCHEMA_VERSION="$(mysql --host="${HOST}" --port="${PORT}" --user=root --batch --skip-column-names \
  --execute="SELECT version_num FROM alembic_version LIMIT 1" "${DATABASE}" 2>/dev/null || true)"
SCHEMA_VERSION="${SCHEMA_VERSION:-unknown}"

ARTIFACT="${BACKUP_DIR}/opbg-${TIMESTAMP}-${SCHEMA_VERSION}.sql.gz.enc"

# The dump is written to a temporary file first: a shell pipeline hides the
# exit status of its first stage, and an empty archive must never be kept.
DUMP="$(mktemp)"
trap 'rm -f "${DUMP}"' EXIT INT TERM

if ! mysqldump \
  --host="${HOST}" \
  --port="${PORT}" \
  --user=root \
  --single-transaction \
  --quick \
  --routines \
  --events \
  --default-character-set=utf8mb4 \
  "${DATABASE}" > "${DUMP}"; then
  echo "mysqldump failed" >&2
  python -m app.cli record-backup --outcome FAILURE --error "mysqldump failed" || true
  exit 1
fi

if ! grep -q "CREATE TABLE" "${DUMP}"; then
  echo "the dump contains no table: refusing to keep an empty backup" >&2
  python -m app.cli record-backup --outcome FAILURE --error "empty dump" || true
  exit 1
fi

gzip -9 < "${DUMP}" \
  | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass "${PASSPHRASE_SOURCE}" \
  > "${ARTIFACT}"

SIZE="$(wc -c < "${ARTIFACT}" | tr -d ' ')"
echo "backup written: ${ARTIFACT} (${SIZE} bytes)"

find "${BACKUP_DIR}" -name 'opbg-*.sql.gz.enc' -type f -mtime "+${RETENTION_DAYS}" -delete

python -m app.cli record-backup \
  --outcome SUCCESS \
  --artifact "$(basename "${ARTIFACT}")" \
  --size "${SIZE}"
