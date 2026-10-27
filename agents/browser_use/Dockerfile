# browser-use runner.
#
# CDP-level agent that pairs a vision-capable LLM with Playwright. browser-use
# runs its own observation/action loop internally; this image only configures
# the LLM and forwards the experiment URL/task.
#
# Build (from agents/):
#   docker build -f browser_use/Dockerfile -t task-complexity/brouse:latest .

FROM task-complexity/agents-base:latest

USER root

# Core deps. langchain-community provides the BaseChatModel surface that
# browser-use expects, and langchain-anthropic / langchain-openai /
# langchain-google-genai provide the concrete LLM bindings (we install all
# three so any --agent-model resolves regardless of provider).
# 2026-05-27: tightened to bounded ranges. browser-use minor releases have
# historically broken Browser/BrowserConfig signatures (see error_log entries);
# pinning <0.2 narrows the blast radius. Lock with `pip freeze` post-build.
RUN pip3 install --no-cache-dir --break-system-packages \
    'browser-use>=0.1.40,<0.2.0' \
    'playwright>=1.45.0,<2.0.0' \
    'langchain-anthropic>=0.2.0,<1.0.0' \
    'langchain-openai>=0.2.0,<1.0.0' \
    'langchain-google-genai>=2.0.0,<3.0.0'

# Use system Chromium (already in the base image) instead of letting
# Playwright download its bundled build. browser-use respects
# `chrome_instance_path` / `executable_path` set in BrowserConfig.

COPY browser_use/runner/ /home/computeruse/runner/
RUN chown -R computeruse:computeruse /home/computeruse/runner

USER computeruse

EXPOSE 6080

ENTRYPOINT ["/home/computeruse/image/entrypoint.sh"]
