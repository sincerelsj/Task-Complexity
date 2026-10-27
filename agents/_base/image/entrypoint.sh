#!/bin/bash
# Shared entrypoint for all framework runners.
#
# Boots the display stack, pre-opens the experiment URL in Chromium, then
# hands off to the framework's runner module. Each framework provides its own
# `runner/run_session.py` (CLI), and the SAME exec line handles all of them.
#
# API key validation is delegated to each runner so this script stays generic.
set -e

cd /home/computeruse

# --- Boot display stack ------------------------------------------------------
/home/computeruse/image/xvfb_startup.sh
/home/computeruse/image/mutter_startup.sh
/home/computeruse/image/x11vnc_startup.sh
/home/computeruse/image/novnc_startup.sh

# --- Pre-open Chromium at the experiment URL --------------------------------
# Extract --url from CLI arguments so the agent's first screenshot already
# shows the page rather than a blank Chromium.
URL=""
args=("$@")
for i in "${!args[@]}"; do
    if [ "${args[$i]}" = "--url" ]; then
        next=$((i + 1))
        URL="${args[$next]}"
        break
    fi
done

if [ "${SKIP_CHROMIUM_AUTO_OPEN}" = "1" ]; then
    echo "[entrypoint] SKIP_CHROMIUM_AUTO_OPEN=1 — runner will launch Chromium itself"
elif [ -n "${URL}" ]; then
    /home/computeruse/image/chromium_startup.sh "${URL}"
else
    echo "[entrypoint] WARNING: --url not provided; Chromium will NOT be pre-opened" >&2
fi

# --- Hand off to the framework's runner -------------------------------------
echo "[entrypoint] launching run_session.py"
exec python3 -m runner.run_session "$@"
