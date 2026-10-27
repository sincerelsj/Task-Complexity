#!/bin/bash
# Launch Chromium in kiosk-ish mode at the provided URL.
# First argument = target URL (passed by entrypoint.sh).
#
# Flags rationale:
#   --no-sandbox                 container-internal; setuid sandbox unavailable
#   --disable-dev-shm-usage      default /dev/shm (64MB) is too small
#   --disable-gpu                Xvfb has no GPU
#   --no-first-run / --no-default-browser-check  skip setup popups
#   --disable-infobars / --test-type  hide info bars; --test-type also removes the
#       "--no-sandbox unsupported flag" warning bar that --disable-infobars does NOT,
#       which otherwise eats top viewport and pushes the experiment grid down
#   --window-size / --window-position  pin to match Computer Use display dims
set -e

URL="${1:?usage: chromium_startup.sh <url>}"

chromium \
    --no-sandbox \
    --disable-dev-shm-usage \
    --disable-gpu \
    --no-first-run \
    --no-default-browser-check \
    --disable-infobars \
    --test-type \
    --disable-session-crashed-bubble \
    --disable-features=TranslateUI \
    --password-store=basic \
    --window-size="${WIDTH},${HEIGHT}" \
    --window-position=0,0 \
    --start-maximized \
    "${URL}" &

# Wait for Chromium to actually render a window (so the first screenshot
# the agent takes shows the experiment page, not a blank screen).
for _ in $(seq 1 100); do
    if xdotool search --name "Chromium" >/dev/null 2>&1; then
        echo "[chromium] window ready"
        sleep 1  # extra settle for page paint
        exit 0
    fi
    sleep 0.2
done

echo "[chromium] WARNING: window didn't appear within 20s; continuing anyway" >&2
