"""Computer Use tool implementation for Claude `computer_20250124`.

Executes actions Claude requests (screenshots, mouse, keyboard) via ``xdotool``
and ``scrot`` against the Xvfb display. Everything here runs inside the
container; the host is never touched.

Reference action set (2025-01-24 tool version):
    screenshot, left_click, right_click, middle_click, double_click,
    triple_click, left_click_drag, mouse_move, left_mouse_down,
    left_mouse_up, cursor_position, key, type, scroll, hold_key, wait
"""

from __future__ import annotations

import base64
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Optional


DISPLAY = os.environ.get("DISPLAY", ":1")
# Real Xvfb resolution. Must match docker-compose.yml WIDTH/HEIGHT and the
# Xvfb -screen geometry in entrypoint.sh. The browser physically renders at
# this resolution; humans observing via noVNC see this.
WIDTH = int(os.environ.get("WIDTH", "1920"))
HEIGHT = int(os.environ.get("HEIGHT", "1200"))  # 16:10 to match MODEL_WIDTH:MODEL_HEIGHT

# Resolution the MODEL sees in screenshots and uses for coordinates.
# Anthropic's computer use models are trained at <=1280x800. Sending higher-res
# screenshots back and expecting native-resolution click coordinates leads to
# systematic off-position misses — the model frames its coords in a ~1280x800
# space regardless of display_width_px we declare, while xdotool interprets the
# numbers as raw Xvfb pixels. Concretely, sonnet-4-6 clicked (644, 238) when
# trying to hit a cell whose actual pixel center was (966, 321) — exactly the
# (1920/1280, 1080/800) = (1.5, 1.35) ratio.
#
# So: downscale screenshots from real -> model resolution before sending, and
# upscale model coordinates -> real before xdotool execution.
MODEL_WIDTH = int(os.environ.get("MODEL_WIDTH", "1280"))
MODEL_HEIGHT = int(os.environ.get("MODEL_HEIGHT", "800"))

# CRITICAL invariant: aspect ratios must match.
# If they don't, downscaling distorts the image non-uniformly and the
# (x, y) scale factors differ — meaning a click on the visual center of a cell
# in the downscaled view does NOT inverse-map to the cell's real pixel center.
# This is exactly what bit us on 1920x1080 (16:9) vs 1280x800 (16:10).
if WIDTH * MODEL_HEIGHT != HEIGHT * MODEL_WIDTH:
    raise RuntimeError(
        f"Aspect-ratio mismatch: real {WIDTH}x{HEIGHT} "
        f"vs model {MODEL_WIDTH}x{MODEL_HEIGHT}. "
        f"They MUST share aspect ratio (currently {WIDTH/HEIGHT:.4f} vs "
        f"{MODEL_WIDTH/MODEL_HEIGHT:.4f}). "
        f"Fix WIDTH/HEIGHT in docker-compose.yml or the corresponding env vars."
    )

SCREENSHOT_DIR = Path("/tmp/screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def _scale_to_real(x: int, y: int) -> tuple[int, int]:
    """Convert MODEL-space (MODEL_WIDTH x MODEL_HEIGHT) coordinates into real
    Xvfb pixel coordinates (WIDTH x HEIGHT) suitable for xdotool."""
    return (
        int(round(x * WIDTH / MODEL_WIDTH)),
        int(round(y * HEIGHT / MODEL_HEIGHT)),
    )


def _scale_to_model(x: int, y: int) -> tuple[int, int]:
    """Inverse of _scale_to_real: real Xvfb pixel coords -> model-space coords.
    Used so cursor_position() returns numbers the model can reuse directly."""
    return (
        int(round(x * MODEL_WIDTH / WIDTH)),
        int(round(y * MODEL_HEIGHT / HEIGHT)),
    )


def _run(cmd: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
    """Run a subprocess with DISPLAY set, raising on non-zero return."""
    env = os.environ.copy()
    env["DISPLAY"] = DISPLAY
    return subprocess.run(
        cmd, env=env, timeout=timeout, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _xdotool(*args: str, timeout: float = 10.0) -> str:
    """Invoke xdotool and return stdout (stripped)."""
    result = _run(["xdotool", *args], timeout=timeout)
    return result.stdout.decode("utf-8", errors="replace").strip()


# --- Screenshot --------------------------------------------------------------

def take_screenshot() -> tuple[bytes, str]:
    """Capture the current Xvfb display to a PNG and downscale to model space.

    Two PNGs are written to /tmp/screenshots/:
        screen_{ts}.png         — real-resolution capture (for trace_logger;
                                  noVNC viewers and human reviewers see this)
        screen_{ts}_model.png   — downscaled to MODEL_WIDTH x MODEL_HEIGHT
                                  (sent to the model)

    Returns (model_png_bytes, model_base64_str). The base64 is what gets
    embedded in the API request — the model NEVER sees the native-resolution
    image, only the downscaled one. This keeps the model's coordinate space
    consistent with what _scale_to_real() expects to invert.
    """
    ts = int(time.time() * 1000)
    real_path = SCREENSHOT_DIR / f"screen_{ts}.png"
    # scrot respects $DISPLAY; no extra flags needed for our Xvfb setup.
    _run(["scrot", "-o", str(real_path)])
    raw = real_path.read_bytes()

    # Downscale to model space so the model's outgoing click coordinates align
    # with what _scale_to_real() expects on the way back. Without this, the
    # model would see a high-res image but try to address it as if it were
    # 1280x800 — leading to ~30% off-position clicks (see error_log #07).
    try:
        from PIL import Image
        import io

        im = Image.open(io.BytesIO(raw))
        if im.size != (WIDTH, HEIGHT):
            im = im.resize((WIDTH, HEIGHT))  # defensive: match Xvfb dims first
        if im.size != (MODEL_WIDTH, MODEL_HEIGHT):
            im = im.resize((MODEL_WIDTH, MODEL_HEIGHT), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        model_bytes = buf.getvalue()
        (SCREENSHOT_DIR / f"screen_{ts}_model.png").write_bytes(model_bytes)
        return model_bytes, base64.standard_b64encode(model_bytes).decode("ascii")
    except Exception:
        # PIL unavailable → fall back to native bytes. The model will be
        # mis-coordinated but the session won't crash.
        return raw, base64.standard_b64encode(raw).decode("ascii")


# --- Mouse -------------------------------------------------------------------

_CLICK_BUTTON = {
    "left_click": 1,
    "middle_click": 2,
    "right_click": 3,
}


# Settle delay after every cursor move. We deliberately do NOT use xdotool's
# `--sync` flag because it blocks waiting for an X MotionNotify event, which is
# never fired when the move target equals the current cursor position. That
# happens often when the agent retries the same coordinate (see error_log #06)
# and produces a 10s subprocess timeout. Plain `mousemove` + a tiny sleep is
# both faster and immune to the no-op-move hang. The follow-up click is
# guaranteed to be processed *after* the move because both go to the same X
# server from the same xdotool client connection (FIFO ordering).
_MOVE_SETTLE_SEC = 0.05


def mouse_move(x: int, y: int) -> None:
    _xdotool("mousemove", str(x), str(y))
    time.sleep(_MOVE_SETTLE_SEC)


def click_at(x: int, y: int, kind: str = "left_click") -> None:
    """Move + click at (x, y). `kind` is one of the CU click action strings."""
    button = _CLICK_BUTTON.get(kind, 1)
    mouse_move(x, y)
    _xdotool("click", str(button))


def double_click(x: int, y: int) -> None:
    mouse_move(x, y)
    _xdotool("click", "--repeat", "2", "--delay", "80", "1")


def triple_click(x: int, y: int) -> None:
    mouse_move(x, y)
    _xdotool("click", "--repeat", "3", "--delay", "80", "1")


def left_click_drag(x1: int, y1: int, x2: int, y2: int) -> None:
    mouse_move(x1, y1)
    _xdotool("mousedown", "1")
    mouse_move(x2, y2)
    _xdotool("mouseup", "1")


def left_mouse_down(x: Optional[int] = None, y: Optional[int] = None) -> None:
    if x is not None and y is not None:
        mouse_move(x, y)
    _xdotool("mousedown", "1")


def left_mouse_up(x: Optional[int] = None, y: Optional[int] = None) -> None:
    if x is not None and y is not None:
        mouse_move(x, y)
    _xdotool("mouseup", "1")


def cursor_position_real() -> tuple[int, int]:
    """Return cursor position in REAL Xvfb pixel coordinates.

    Caller is responsible for converting to model space if the value is going
    back to the model (use _scale_to_model). Within tools.py we use real coords
    everywhere xdotool is involved.
    """
    out = _xdotool("getmouselocation", "--shell")
    # output lines: X=...\nY=...\nSCREEN=...\nWINDOW=...
    kv = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return int(kv.get("X", 0)), int(kv.get("Y", 0))


# --- Keyboard ----------------------------------------------------------------

def press_key(text: str) -> None:
    """Press a named key or combination. xdotool syntax: ``ctrl+a``, ``Return``,
    ``Tab``, ``Escape``, etc.
    """
    _xdotool("key", "--", text)


def type_text(text: str) -> None:
    """Type a literal string."""
    _xdotool("type", "--delay", "12", "--", text)


def hold_key(text: str, duration: float) -> None:
    """Hold a key for `duration` seconds."""
    _xdotool("keydown", "--", text)
    try:
        time.sleep(duration)
    finally:
        _xdotool("keyup", "--", text)


# --- Scroll ------------------------------------------------------------------

_SCROLL_BUTTON = {"up": 4, "down": 5, "left": 6, "right": 7}


def scroll(x: int, y: int, direction: str, amount: int) -> None:
    """Scroll ``amount`` clicks in ``direction`` after moving to (x,y)."""
    button = _SCROLL_BUTTON.get(direction.lower(), 5)
    mouse_move(x, y)
    _xdotool("click", "--repeat", str(max(1, int(amount))), "--delay", "30", str(button))


# --- Top-level dispatch ------------------------------------------------------

def execute_action(tool_input: dict[str, Any]) -> dict[str, Any]:
    """Execute one Computer Use action.

    Returns a dict suitable for inclusion in a ``tool_result`` message back to
    Claude. Screenshots are attached as base64 image content blocks by the
    caller (see loop.py) — this function only reports text outcomes.

    The contract:
        {"text": "<optional text feedback>",
         "screenshot_b64": "<base64 PNG string or None>",
         "error": "<str or None>"}
    """
    action = tool_input.get("action", "")
    coord = tool_input.get("coordinate")
    text = tool_input.get("text")

    # All coordinates from the model are in MODEL_WIDTH x MODEL_HEIGHT space.
    # Convert to real Xvfb pixel space at the boundary, before any mouse helper
    # touches xdotool. cursor_position() inverts the mapping on the way out.
    def _coord(c):
        if c is None:
            return None
        return _scale_to_real(int(c[0]), int(c[1]))

    try:
        if action == "screenshot":
            _, b64 = take_screenshot()
            return {"text": None, "screenshot_b64": b64, "error": None}

        if action in _CLICK_BUTTON:
            x, y = _coord(coord)
            click_at(x, y, kind=action)
        elif action == "double_click":
            x, y = _coord(coord)
            double_click(x, y)
        elif action == "triple_click":
            x, y = _coord(coord)
            triple_click(x, y)
        elif action == "left_click_drag":
            # 2025-01-24 spec sends start_coordinate + coordinate (end).
            # If only coordinate is present, drag from current cursor position.
            end = _coord(tool_input.get("coordinate"))
            start_raw = tool_input.get("start_coordinate")
            if start_raw is None:
                start = cursor_position_real()  # already in real space
            else:
                start = _coord(start_raw)
            left_click_drag(int(start[0]), int(start[1]), int(end[0]), int(end[1]))
        elif action == "mouse_move":
            x, y = _coord(coord)
            mouse_move(x, y)
        elif action == "left_mouse_down":
            xy = _coord(coord)
            left_mouse_down(*(xy if xy is not None else (None, None)))
        elif action == "left_mouse_up":
            xy = _coord(coord)
            left_mouse_up(*(xy if xy is not None else (None, None)))
        elif action == "cursor_position":
            # cursor_position() returns real Xvfb coords; convert back to model
            # space so the model's next click-with-this-coord round-trips.
            rx, ry = cursor_position_real()
            mx, my = _scale_to_model(rx, ry)
            return {"text": f"X={mx}, Y={my}", "screenshot_b64": None, "error": None}
        elif action == "key":
            press_key(text or "")
        elif action == "type":
            type_text(text or "")
        elif action == "hold_key":
            duration = float(tool_input.get("duration", 1.0))
            hold_key(text or "", duration)
        elif action == "scroll":
            x, y = _coord(coord)
            direction = tool_input.get("scroll_direction", "down")
            amount = int(tool_input.get("scroll_amount", 3))
            scroll(x, y, direction, amount)
        elif action == "wait":
            duration = float(tool_input.get("duration", 1.0))
            time.sleep(duration)
        else:
            return {"text": None, "screenshot_b64": None,
                    "error": f"unsupported action: {action!r}"}

        # After any state-changing action, take a fresh screenshot so Claude
        # sees the result on its next turn. Small settle delay so the UI
        # repaints before we capture.
        time.sleep(0.4)
        _, b64 = take_screenshot()
        return {"text": None, "screenshot_b64": b64, "error": None}

    except subprocess.CalledProcessError as e:
        return {"text": None, "screenshot_b64": None,
                "error": f"{action} failed: {e.stderr.decode('utf-8', 'replace')[:500]}"}
    except Exception as e:
        return {"text": None, "screenshot_b64": None,
                "error": f"{action} failed: {e}"}
