# Shared base image for all Task Complexity agent runners.
#
# Provides the display stack (Xvfb + mutter + Chromium + x11vnc + noVNC),
# Korean locale, and the core /home/computeruse layout. Each framework's own
# Dockerfile FROMs this image and only adds its provider-specific Python deps
# and runner code.
#
# Build (from agents/ — i.e. the parent of _base/):
#   docker build -f _base/Dockerfile -t task-complexity/agents-base:latest .
#
# Build context MUST be the parent `agents/` directory so registry.py and
# __init__.py can be COPYd in.

FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Seoul

# --- System packages ---------------------------------------------------------
# Xvfb + xdotool: virtual display + input injection (used by pixel-level fws)
# x11vnc + novnc + websockify: live observation at :6080
# mutter + dbus-x11: window manager + session bus (Chromium stability)
# chromium + fonts-noto-cjk: browser + Korean rendering
# scrot + imagemagick: screenshot capture / image conversion
# python3 + pip + venv: runner runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl wget gnupg \
    sudo \
    xvfb xdotool xauth x11-utils \
    x11vnc novnc websockify \
    mutter dbus-x11 \
    chromium \
    fonts-noto-cjk fonts-liberation \
    scrot imagemagick \
    python3 python3-pip python3-venv \
    locales tzdata \
  && rm -rf /var/lib/apt/lists/*

# --- Locale ------------------------------------------------------------------
RUN sed -i '/ko_KR.UTF-8/s/^# //g' /etc/locale.gen \
  && sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen \
  && locale-gen \
  && fc-cache -fv

ENV LANG=ko_KR.UTF-8 \
    LANGUAGE=ko_KR:ko \
    LC_ALL=ko_KR.UTF-8

# --- Non-root user (uid 1000 mirrors Anthropic CU demo convention) -----------
RUN useradd -m -s /bin/bash -u 1000 computeruse \
  && echo "computeruse ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# --- Display configuration ---------------------------------------------------
# These MUST match the display dims declared to each provider's computer-use
# tool (loop.py in each runner). Override via docker-compose if needed.
ENV DISPLAY_NUM=1 \
    DISPLAY=:1 \
    HEIGHT=800 \
    WIDTH=1280

# --- Shared assets -----------------------------------------------------------
WORKDIR /home/computeruse

# Display stack scripts (xvfb / mutter / chromium / x11vnc / novnc / entrypoint).
COPY _base/image/ /home/computeruse/image/

# Cross-framework registry for subject_id / trace folder conventions.
COPY __init__.py /home/computeruse/agents/__init__.py
COPY registry.py /home/computeruse/agents/registry.py

# Shared trace logger module — every framework writes Layer-2 traces in this
# format so post-hoc comparison is possible.
COPY _shared/ /home/computeruse/shared/

RUN chmod +x /home/computeruse/image/*.sh \
  && chown -R computeruse:computeruse /home/computeruse

# Pre-create /tmp/.X11-unix with the canonical 1777 (sticky, world-writable)
# permissions. Without this, Xvfb running as the non-root `computeruse` user
# emits a benign-but-noisy stderr line at startup:
#   _XSERVTransmkdir: ERROR: euid != 0,directory /tmp/.X11-unix will not be created.
# The display still works (Xvfb falls back to listening), but the stderr line
# triggers PowerShell wrappers that use `*>&1 | Tee-Object` with
# `$ErrorActionPreference = "Stop"` to abort the pipeline and falsely report
# the whole run as exit=99. See error_log #08.
RUN mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix

USER computeruse

EXPOSE 6080

# Each framework's Dockerfile sets its own ENTRYPOINT (entrypoint.sh wrapped
# around its own runner module). The base image deliberately does NOT define
# one — it is meant to be FROM'd, not run directly.
