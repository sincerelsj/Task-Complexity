#!/bin/bash
# Bridge VNC (:5900) to a browser-accessible noVNC page at :6080.
# Open http://localhost:6080/vnc.html from the Windows host to watch live.
set -e

websockify --web=/usr/share/novnc/ 6080 localhost:5900 >/tmp/novnc.log 2>&1 &
echo "[novnc] http://localhost:6080/vnc.html"
