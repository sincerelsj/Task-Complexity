#!/bin/bash
# Main entrypoint: boot display stack, open URL in Chromium, then hand off to
# the Python agent runner. All state from here is ephemeral to the container
# except what lives under /workspace/traces (mounted volume) and what the
# agent's clicks cause the host Flask app to write via /log and /submit.
set -e

# Required env: ANTHROPIC_API_KEY
if [ -z "${ANTHROPIC_API_KEY}" ]; then
    echo "ERROR: ANTHROPIC_API_KEY is not set" >&2
    exit 1
fi

# Parse runner arguments (everything after -- is forwarded to run_session.py).
# Expected pattern:
#   docker compose run --rm agent <ALL_CLI_ARGS>
# where CLI_ARGS include --url, --task, --subject-id, --condition-id, --model.

cd /home/computeruse

# --- Boot display stack ------------------------------------------------------
/home/computeruse/image/xvfb_startup.sh
/home/computeruse/image/mutter_startup.sh
/home/computeruse/image/x11vnc_startup.sh
/home/computeruse/image/novnc_startup.sh

# Extract --url from arguments (to pre-open Chromium before the agent starts).
URL=""
args=("$@")
for i in "${!args[@]}"; do
    if [ "${args[$i]}" = "--url" ]; then
        next=$((i + 1))
        URL="${args[$next]}"
        break
    fi
done

if [ -n "${URL}" ]; then
    /home/computeruse/image/chromium_startup.sh "${URL}"
else
    echo "[entrypoint] WARNING: --url not provided; Chromium will NOT be pre-opened" >&2
fi

# --- Hand off to the Python runner ------------------------------------------
echo "[entrypoint] launching run_session.py"
exec python3 -m runner.run_session "$@"
