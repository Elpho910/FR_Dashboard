#!/bin/sh
set -eu

APP_ROLE="${APP_ROLE:-server}"

case "$APP_ROLE" in
  server|client)
    ;;
  *)
    echo "Unsupported APP_ROLE: $APP_ROLE" >&2
    exit 1
    ;;
esac

exec gunicorn \
  --bind 0.0.0.0:"${PORT:-5000}" \
  --workers 1 \
  --threads 4 \
  --timeout 60 \
  app:app
