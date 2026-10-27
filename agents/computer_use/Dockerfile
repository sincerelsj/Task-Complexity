# Anthropic Computer Use runner — Task Complexity.
#
# Now FROMs the shared base image (display stack + Korean locale +
# agents/registry + shared trace logger) like the other 5 frameworks.
# Pre-2026-05-13 this Dockerfile installed the whole stack itself
# (FROM debian:bookworm-slim) which caused silent drift from the other
# frameworks (e.g. the /tmp/.X11-unix fix from base didn't reach ANTHCU).
# See error_log #08 / #09.
#
# Build (from agents/):
#   docker build -f computer_use/Dockerfile -t task-complexity/anthropic-cu:latest .
# Or via docker-compose:
#   docker compose -f computer_use/docker-compose.yml build

FROM task-complexity/agents-base:latest

USER root

# Provider SDK + screenshot downscaling.
# 2026-05-27: tightened from open floor (`>=`) to bounded range to prevent
# minor-version drift. For full academic reproducibility, run `pip freeze`
# after build and commit a lock file. See PROJECT_STATUS.md backlog.
RUN pip3 install --no-cache-dir --break-system-packages \
    'anthropic>=0.40.0,<1.0.0' \
    'Pillow>=10.0.0,<12.0.0'

# This framework's runner. Build context is `agents/`.
COPY computer_use/runner/ /home/computeruse/runner/
RUN chown -R computeruse:computeruse /home/computeruse/runner

# Display configuration — defaults overridden by docker-compose.yml.
# Real Xvfb runs at WIDTH x HEIGHT while the model sees MODEL_WIDTH x MODEL_HEIGHT
# (downscaled screenshots, upscaled coordinates in tools.py). Aspect ratio MUST
# match: 1280:800 = 1920:1200 = 16:10. See error_log #07.
ENV WIDTH=1280 \
    HEIGHT=800 \
    MODEL_WIDTH=1280 \
    MODEL_HEIGHT=800

USER computeruse

EXPOSE 6080

ENTRYPOINT ["/home/computeruse/image/entrypoint.sh"]
