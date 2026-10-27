#!/bin/bash
# Start Xvfb (X virtual framebuffer) at the configured display number and size.
# This provides a headless X11 display that Chromium and xdotool can target.
set -e

XVFB_WHD="${WIDTH}x${HEIGHT}x24"

# Wait for any existing Xvfb on this display to exit.
if xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1; then
    echo "[xvfb] display ${DISPLAY} already active; skipping"
    exit 0
fi

Xvfb "${DISPLAY}" -screen 0 "${XVFB_WHD}" -ac +extension RANDR +render -noreset &
XVFB_PID=$!

# Wait for Xvfb to become ready (up to 10s).
for _ in $(seq 1 50); do
    if xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1; then
        echo "[xvfb] ready on ${DISPLAY} (${XVFB_WHD}) pid=${XVFB_PID}"
        exit 0
    fi
    sleep 0.2
done

echo "[xvfb] FAILED to start on ${DISPLAY}" >&2
exit 1
