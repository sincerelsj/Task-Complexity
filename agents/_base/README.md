# Shared Browser Runtime

The base image provides Debian, Chromium, Xvfb, mutter, xdotool, noVNC, Python, and CJK fonts. Build once before the six framework images that inherit it:

```powershell
Push-Location agents
docker build -f _base/Dockerfile -t task-complexity/agents-base:latest .
Pop-Location
```

Run from the repository root. The `agents/` build context contains shared code and the registry. All six framework images reuse this base. The base includes the shared modules used by all session runners.

The real display is 1920×1200. Applicable pixel providers use a 1280×800 coordinate space; preserve the common 16:10 ratio. CDP runners launch their own Chromium with `SKIP_CHROMIUM_AUTO_OPEN=1`.
