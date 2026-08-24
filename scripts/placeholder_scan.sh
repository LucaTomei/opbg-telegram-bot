#!/usr/bin/env bash
# Fail when production code still contains placeholders or unimplemented paths.
#
# Run it with `bash scripts/placeholder_scan.sh`. It is also part of the CI
# guardrails job.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

status=0

report() {
  echo "FAIL: $1" >&2
  status=1
}

# The scanners necessarily spell out the patterns they look for, so they never
# scan themselves. Every other file under scripts/ is still checked.
EXCLUDE=(':!scripts/placeholder_scan.sh' ':!scripts/secret_scan.sh')

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

# 1. Leftover markers anywhere in shipped code.
scan "placeholder marker" '\b(TODO|FIXME|XXX|HACK)\b' \
  -- app alembic docker scripts "${EXCLUDE[@]}"

# 2. Bare `pass` bodies in application code.
scan "bare pass statement" '^[[:space:]]*pass[[:space:]]*$' \
  -- app alembic "${EXCLUDE[@]}"

# 3. Ellipsis bodies outside Protocol declarations.
ellipsis="$(git grep -nEI '^[[:space:]]*\.\.\.[[:space:]]*$' -- app "${EXCLUDE[@]}" 2>/dev/null \
  | grep -v 'Protocol' || true)"
if [ -n "${ellipsis}" ]; then
  while IFS= read -r line; do
    [ -n "${line}" ] && report "ellipsis body: ${line}"
  done <<< "${ellipsis}"
fi

# 4. Only a *raised* NotImplementedError is a placeholder; suppressing the one
#    asyncio raises for signal handlers on unsupported platforms is legitimate.
scan "unimplemented path" 'raise[[:space:]]+NotImplementedError' \
  -- app alembic "${EXCLUDE[@]}"

if [ "${status}" -eq 0 ]; then
  echo "placeholder scan: clean"
fi
exit "${status}"
