#!/usr/bin/env bash
# Fail when the repository contains secrets, captures or real personal data.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

status=0

report() {
  echo "FAIL: $1" >&2
  status=1
}

# 1. Forbidden files.
while IFS= read -r file; do
  report "forbidden file committed: ${file}"
done < <(git ls-files -- '*.har' '*.pdf' '.env' '.env.*' '*.pem' '*.key' '*.p12' '*.pfx' \
          | grep -v '^\.env\.example$' || true)

# 2. Untracked capture files anywhere in the tree.
while IFS= read -r file; do
  report "capture file present in the working tree: ${file}"
done < <(find . -path ./.git -prune -o -path ./.venv -prune -o \
          \( -name '*.har' -o -name '*.pdf' \) -print 2>/dev/null || true)

SCAN_PATHS=(app alembic tests scripts docker docs README.md compose.yaml compose.override.yaml Dockerfile)

# 3. Italian tax codes outside the documented synthetic fixtures.
if git grep -nEI '\b[A-Z]{6}[0-9]{2}[A-Z][0-9]{2}[A-Z][0-9]{3}[A-Z]\b' -- "${SCAN_PATHS[@]}" \
    | grep -v 'tests/fixtures/synthetic' \
    | grep -v 'tests/' \
    | grep -v 'redaction' > /tmp/opbg_taxcodes.txt 2>/dev/null; then
  if [ -s /tmp/opbg_taxcodes.txt ]; then
    while IFS= read -r line; do report "possible tax code: ${line}"; done < /tmp/opbg_taxcodes.txt
  fi
fi
rm -f /tmp/opbg_taxcodes.txt

# 4. Hardcoded credential-looking assignments.
if git grep -nEI "(password|passwd|secret|token|api_key)[[:space:]]*=[[:space:]]*[\"'][^\"']{12,}[\"']" \
    -- app alembic scripts docker > /tmp/opbg_secrets.txt 2>/dev/null; then
  if [ -s /tmp/opbg_secrets.txt ]; then
    while IFS= read -r line; do report "possible hardcoded secret: ${line}"; done < /tmp/opbg_secrets.txt
  fi
fi
rm -f /tmp/opbg_secrets.txt

# 5. Private keys and Telegram bot tokens.
if git grep -nI -- '-----BEGIN .*PRIVATE KEY-----' > /dev/null 2>&1; then
  report "a private key is committed"
fi
if git grep -nEI '\b[0-9]{8,10}:AA[0-9A-Za-z_-]{33}\b' -- "${SCAN_PATHS[@]}" > /dev/null 2>&1; then
  report "a Telegram bot token pattern is committed"
fi

# 6. The real portal host must not appear inside fixtures.
if git grep -nI 'prenotazioni.ospedalebambinogesu.it' -- tests > /dev/null 2>&1; then
  report "fixtures reference the real portal host"
fi

if [ "${status}" -eq 0 ]; then
  echo "secret scan: clean"
fi
exit "${status}"
