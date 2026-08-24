#!/bin/sh
# Container entrypoint: selects the process to run inside the shared image.
set -eu

command="${1:-web}"

case "${command}" in
  web)
    exec python -m app.main_web
    ;;
  bot)
    exec python -m app.main_bot
    ;;
  worker)
    exec python -m app.main_worker
    ;;
  migrate)
    exec python -m app.cli migrate
    ;;
  healthcheck)
    exec python -m app.cli healthcheck
    ;;
  shell)
    exec python
    ;;
  *)
    exec "$@"
    ;;
esac
