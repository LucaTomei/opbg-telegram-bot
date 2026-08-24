#!/usr/bin/env bash
# Fail when the repository contains secrets, captures or real personal data.
#
# Run it with `bash scripts/secret_scan.sh`. It is also the first job of the CI
# workflow, so a finding blocks the pipeline before anything else runs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

status=0

report() {
  echo "FAIL: $1" >&2
  status=1
}

# Paths that must never contain a secret or a real personal datum.
SCAN_PATHS=(
  app alembic tests scripts docker docs .github
  README.md compose.yaml compose.dev.yaml Dockerfile
)

# The scanners necessarily spell out the patterns they hunt for, so they are
# excluded from their own search. Every other file is still scanned.
EXCLUDE=(':!scripts/secret_scan.sh' ':!scripts/placeholder_scan.sh')

# Collect the matches of one pattern and report them under a label.
scan() {
  local label="$1"
  shift
  local output
  output="$(git grep -nEI "$@" || true)"
  if [ -n "${output}" ]; then
    while IFS= read -r line; do
      [ -n "${line}" ] && report "${label}: ${line}"
    done <<< "${output}"
  fi
}

# 1. Forbidden files must never be tracked.
forbidden="$(git ls-files -- '*.har' '*.pdf' '.env' '.env.*' '*.pem' '*.key' '*.p12' '*.pfx' \
  | grep -v '^\.env\.example$' || true)"
if [ -n "${forbidden}" ]; then
  while IFS= read -r file; do
    [ -n "${file}" ] && report "forbidden file committed: ${file}"
  done <<< "${forbidden}"
fi

# 2. Capture files must not even sit in the working tree.
captures="$(find . -path ./.git -prune -o -path ./.venv -prune -o \
  \( -name '*.har' -o -name '*.pdf' \) -print 2>/dev/null || true)"
if [ -n "${captures}" ]; then
  while IFS= read -r file; do
    [ -n "${file}" ] && report "capture file in the working tree: ${file}"
  done <<< "${captures}"
fi

# 3. Italian tax codes outside the documented synthetic fixtures.
taxcodes="$(git grep -nEI '\b[A-Z]{6}[0-9]{2}[A-Z][0-9]{2}[A-Z][0-9]{3}[A-Z]\b' \
  -- "${SCAN_PATHS[@]}" "${EXCLUDE[@]}" 2>/dev/null \
  | grep -v '^tests/' | grep -v 'redaction' || true)"
if [ -n "${taxcodes}" ]; then
  while IFS= read -r line; do
    [ -n "${line}" ] && report "possible tax code: ${line}"
  done <<< "${taxcodes}"
fi

# 4. Literal credential assignments. Case insensitive, otherwise API_KEY and
#    PASSWORD slip through. Values that expand a variable are not secrets.
scan "possible hardcoded secret" -i \
  "(password|passwd|secret|token|api_key)[[:space:]]*=[[:space:]]*[\"'][^\"'\$]{12,}[\"']" \
  -- app alembic scripts docker "${EXCLUDE[@]}"

# 5. Private keys.
scan "a private key is committed" -- '-----BEGIN .*PRIVATE KEY-----' \
  -- "${SCAN_PATHS[@]}" "${EXCLUDE[@]}"

# 6. Telegram bot tokens. A real one is "<bot id>:<35 characters>"; the bound is
#    deliberately loose so a slightly different shape is still caught.
scan "possible Telegram bot token" '[0-9]{6,12}:AA[0-9A-Za-z_-]{30,}' \
  -- "${SCAN_PATHS[@]}" "${EXCLUDE[@]}"

# 7. The real portal host must not appear inside the fixtures.
scan "fixture references the real portal host" 'prenotazioni\.ospedalebambinogesu\.it' \
  -- tests "${EXCLUDE[@]}"

if [ "${status}" -eq 0 ]; then
  echo "secret scan: clean"
fi
exit "${status}"
