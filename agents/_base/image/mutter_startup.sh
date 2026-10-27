#!/bin/bash
# Start a minimal window manager. Chromium is more stable with a WM running
# (focus handling, window placement). Mutter in X11 mode needs a DBus session
# so we launch one on first invocation.
set -e

# Start a DBus session if one isn't already wired up.
if [ -z "${DBUS_SESSION_BUS_ADDRESS}" ]; then
    eval "$(dbus-launch --sh-syntax)"
    export DBUS_SESSION_BUS_ADDRESS DBUS_SESSION_BUS_PID
fi

XDG_SESSION_TYPE=x11 mutter --x11 --sm-disable --replace >/tmp/mutter.log 2>&1 &
sleep 1
echo "[mutter] started (dbus=${DBUS_SESSION_BUS_ADDRESS})"
