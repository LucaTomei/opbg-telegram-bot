#!/usr/bin/env bash
# Fail when production code still contains placeholders or unimplemented paths.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

status=0

report() {
  echo "FAIL: $1" >&2
  status=1
}

# TODO/FIXME/XXX/HACK markers anywhere in shipped code.
if git grep -nEI '\b(TODO|FIXME|XXX|HACK)\b' -- app alembic docker scripts > /tmp/opbg_todo.txt 2>/dev/null; then
  if [ -s /tmp/opbg_todo.txt ]; then
    while IFS= read -r line; do report "placeholder marker: ${line}"; done < /tmp/opbg_todo.txt
  fi
fi
rm -f /tmp/opbg_todo.txt

# Bare `pass`, `...` bodies and NotImplementedError in application code.
if git grep -nEI '^[[:space:]]*pass[[:space:]]*$' -- app alembic > /tmp/opbg_pass.txt 2>/dev/null; then
  if [ -s /tmp/opbg_pass.txt ]; then
    while IFS= read -r line; do report "bare pass statement: ${line}"; done < /tmp/opbg_pass.txt
  fi
fi
rm -f /tmp/opbg_pass.txt

if git grep -nEI '^[[:space:]]*\.\.\.[[:space:]]*$' -- app | grep -v 'Protocol' > /tmp/opbg_ellipsis.txt 2>/dev/null; then
  if [ -s /tmp/opbg_ellipsis.txt ]; then
    while IFS= read -r line; do report "ellipsis body: ${line}"; done < /tmp/opbg_ellipsis.txt
  fi
fi
rm -f /tmp/opbg_ellipsis.txt

# Only a *raised* NotImplementedError is a placeholder; suppressing the one
# raised by asyncio signal handlers on unsupported platforms is legitimate.
if git grep -nEI 'raise[[:space:]]+NotImplementedError' -- app alembic > /tmp/opbg_ni.txt 2>/dev/null; then
  if [ -s /tmp/opbg_ni.txt ]; then
    while IFS= read -r line; do report "unimplemented path: ${line}"; done < /tmp/opbg_ni.txt
  fi
fi
rm -f /tmp/opbg_ni.txt

if [ "${status}" -eq 0 ]; then
  echo "placeholder scan: clean"
fi
exit "${status}"
