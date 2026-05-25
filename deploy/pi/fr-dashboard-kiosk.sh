#!/usr/bin/env bash
set -euo pipefail

BOARD_URL="${BOARD_URL:-http://127.0.0.1:5000/}"
CHROMIUM_BIN="${CHROMIUM_BIN:-/usr/bin/chromium-browser}"

until curl -fsS "$BOARD_URL" >/dev/null 2>&1; do
  sleep 2
done

exec "$CHROMIUM_BIN"   --kiosk   --incognito   --noerrdialogs   --disable-infobars   --disable-features=Translate   --check-for-update-interval=31536000   "$BOARD_URL"
