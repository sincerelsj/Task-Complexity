# OpenAI Computer Use runner.
#
# FROMs the shared base image (display stack + Korean locale + agents/registry
# + shared trace logger) and only adds the OpenAI SDK + this framework's
# runner code.
#
# Build (from agents/):
#   docker build -f openai_computer_use/Dockerfile -t task-complexity/oaicu:latest .
#
# Or via docker-compose, which handles the build automatically:
#   docker compose -f openai_computer_use/docker-compose.yml build

FROM task-complexity/agents-base:latest

USER root

# Provider SDK + dependencies. Pillow is needed for screenshot downscaling
# (converting MODEL_WIDTH x MODEL_HEIGHT for the model to work with real
# Xvfb resolution for xdotool).
# 2026-05-27: tightened to bounded ranges. Lock with `pip freeze` post-build.
RUN pip3 install --no-cache-dir --break-system-packages \
    'openai>=1.50.0,<2.0.0' \
    'Pillow>=10.0.0,<12.0.0'

# This framework's runner. Build context is `agents/` so the path is relative.
COPY openai_computer_use/runner/ /home/computeruse/runner/
RUN chown -R computeruse:computeruse /home/computeruse/runner

# Display configuration — defaults overridden by docker-compose.yml at runtime.
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
