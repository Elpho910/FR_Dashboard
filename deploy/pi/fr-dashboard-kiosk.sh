#!/usr/bin/env bash
set -euo pipefail

BOARD_URL="${BOARD_URL:-http://127.0.0.1:5000/}"

if [[ -z "${CHROMIUM_BIN:-}" ]]; then
  for candidate in /usr/bin/chromium-browser /usr/bin/chromium; do
    if [[ -x "$candidate" ]]; then
      CHROMIUM_BIN="$candidate"
      break
    fi
  done
fi

if [[ -z "${CHROMIUM_BIN:-}" ]]; then
  echo "Could not find Chromium. Set CHROMIUM_BIN to the browser path." >&2
  exit 1
fi

until curl -fsS "$BOARD_URL" >/dev/null 2>&1; do
  sleep 2
done

exec "$CHROMIUM_BIN"   --kiosk   --incognito   --noerrdialogs   --disable-infobars   --disable-features=Translate   --check-for-update-interval=31536000   "$BOARD_URL"
