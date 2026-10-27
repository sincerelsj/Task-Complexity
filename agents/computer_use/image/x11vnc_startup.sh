#!/bin/bash
# Expose the Xvfb display over VNC on :5900 so noVNC can bridge it to browser.
set -e

x11vnc \
    -display "${DISPLAY}" \
    -forever \
    -shared \
    -nopw \
    -rfbport 5900 \
    -quiet &
echo "[x11vnc] listening on :5900"
