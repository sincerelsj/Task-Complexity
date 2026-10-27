# Gemini Computer Use runner for Task Complexity experiment.
#
# Based on anthropic-cu with Gemini SDK swap. Coordinate space: Gemini outputs
# 0-1000 normalized coords which we convert to 0-1280x800 (MODEL space) and then
# to 1920x1200 real pixels. Aspect ratio: 1920:1200 = 1280:800 = 16:10 (critical).
#
# Build (from agents/):
#   docker build -f gemini_computer_use/Dockerfile -t task-complexity/gemcu:latest .

FROM task-complexity/agents-base:latest

USER root

# Provider SDK. The Computer Use tool surface lives in google-genai (the new
# unified Python SDK), NOT the older `google-generativeai` package.
# Also add Pillow for screenshot downscaling to model space.
# 2026-05-27: tightened to bounded ranges. Lock with `pip freeze` post-build.
RUN pip3 install --no-cache-dir --break-system-packages \
    'google-genai>=0.5.0,<2.0.0' \
    'Pillow>=10.0.0,<12.0.0'

# --- Display configuration ---------------------------------------------------
# Defaults — overridden by docker-compose.yml. Real Xvfb runs at WIDTH x HEIGHT
# while the model sees MODEL_WIDTH x MODEL_HEIGHT (downscaled screenshots).
# Aspect ratio MUST match between the two pairs — 1280:800 = 1920:1200 = 16:10.
ENV DISPLAY_NUM=1 \
    DISPLAY=:1 \
    WIDTH=1280 \
    HEIGHT=800 \
    MODEL_WIDTH=1280 \
    MODEL_HEIGHT=800

COPY gemini_computer_use/runner/ /home/computeruse/runner/
RUN chown -R computeruse:computeruse /home/computeruse/runner

USER computeruse

EXPOSE 6080

ENTRYPOINT ["/home/computeruse/image/entrypoint.sh"]
