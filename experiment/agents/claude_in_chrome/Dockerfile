# Claude-in-Chrome runner.
#
# Anthropic API + custom Chrome-extension-shaped tools, executed via
# Playwright/CDP. The runner launches its own Chromium instance (reusing the
# system /usr/bin/chromium binary so we don't double-download), so the base
# entrypoint should NOT pre-open a Chromium window — set
# SKIP_CHROMIUM_AUTO_OPEN=1 in docker-compose.
#
# Build (from agents/):
#   docker build -f claude_in_chrome/Dockerfile -t task-complexity/cldcr:latest .

FROM task-complexity/agents-base:latest

USER root

# anthropic for the model API; playwright for CDP-level browser control.
# 2026-05-27: tightened to bounded ranges. Lock with `pip freeze` post-build.
RUN pip3 install --no-cache-dir --break-system-packages \
    'anthropic>=0.40.0,<1.0.0' \
    'Pillow>=10.0.0,<12.0.0' \
    'playwright>=1.45.0,<2.0.0'

# We do NOT run `playwright install chromium` because the runner uses the
# system Chromium installed in the base image (executable_path=/usr/bin/chromium).
# This keeps the image smaller and ensures the same Chromium version + Korean
# fonts the pixel-level runners use.

COPY claude_in_chrome/runner/ /home/computeruse/runner/
RUN chown -R computeruse:computeruse /home/computeruse/runner

USER computeruse

EXPOSE 6080

ENTRYPOINT ["/home/computeruse/image/entrypoint.sh"]
