"""xdotool-based action dispatcher for OpenAI's Computer Use tool.

Originally written for the `computer-use-preview` model + `computer_use_preview`
tool. After the 2026-06-02 migration to `gpt-5.4-mini` + GA `"computer"` tool,
the action payload shape is unchanged — only the tool-type string differs
(handled in loop.py via OAICU_TOOL_TYPE env var). Each action arrives as a
``computer_call`` item in the Responses API output with a typed ``action``
payload.

Supported action types:
    click        {x, y, button: "left"|"right"|"middle"|"wheel"}
    double_click {x, y}
    type         {text}
    keypress     {keys: list[str]}        # e.g. ["CTRL", "A"] or ["ENTER"]
    scroll       {x, y, scroll_x, scroll_y}
    move         {x, y}
    drag         {path: list[{x, y}]}
    wait         {}
    screenshot   {}

We translate each into ``xdotool`` calls against the Xvfb display and return a
fresh PNG screenshot in base64 for the next API turn. Coordinates are scaled
to match the model's view (MODEL_WIDTH x MODEL_HEIGHT) even though Xvfb renders
at real resolution (WIDTH x HEIGHT).
"""

from __future__ import annotations

import base64
import io
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Optional


DISPLAY = os.environ.get("DISPLAY", ":1")
# Real Xvfb resolution. Must match docker-compose.yml WIDTH/HEIGHT and the
# Xvfb -screen geometry in entrypoint.sh. The browser physically renders at
# this resolution; humans observing via noVNC see this.
WIDTH = int(os.environ.get("WIDTH", "1280"))
HEIGHT = int(os.environ.get("HEIGHT", "800"))

# Resolution the MODEL sees in screenshots and uses for coordinates.
# OpenAI's computer use models are trained at <=1280x800. Sending higher-res
# screenshots back and expecting native-resolution click coordinates leads to
# systematic off-position misses — the model frames its coords in a ~1280x800
# space regardless of display_width we declare, while xdotool interprets the
# numbers as raw Xvfb pixels. So: downscale screenshots from real -> model
# resolution before sending, and upscale model coordinates -> real before
# xdotool execution.
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
    Used so we return numbers the model can reuse directly in follow-up actions."""
    return (
        int(round(x * MODEL_WIDTH / WIDTH)),
        int(round(y * MODEL_HEIGHT / HEIGHT)),
    )


def _run(cmd: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["DISPLAY"] = DISPLAY
    return subprocess.run(
        cmd, env=env, timeout=timeout, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _xdotool(*args: str, timeout: float = 10.0) -> str:
    return _run(["xdotool", *args], timeout=timeout).stdout.decode(
        "utf-8", errors="replace"
    ).strip()


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


# --- Mouse helpers -----------------------------------------------------------

_BUTTON = {"left": 1, "middle": 2, "right": 3, "wheel": 1}

# Settle delay after every cursor move. We deliberately do NOT use xdotool's
# `--sync` flag because it blocks waiting for an X MotionNotify event, which is
# never fired when the move target equals the current cursor position. That
# happens often when the agent retries the same coordinate (see error_log #06)
# and produces a 10s subprocess timeout. Plain `mousemove` + a tiny sleep is
# both faster and immune to the no-op-move hang. The follow-up click is
# guaranteed to be processed *after* the move because both go to the same X
# server from the same xdotool client connection (FIFO ordering).
_MOVE_SETTLE_SEC = 0.05


def _move(x: int, y: int) -> None:
    _xdotool("mousemove", str(x), str(y))
    time.sleep(_MOVE_SETTLE_SEC)


def _click_button(button: str) -> None:
    _xdotool("click", str(_BUTTON.get(button.lower(), 1)))


# --- Keypress translation ----------------------------------------------------
# OpenAI sends key names like "ENTER", "CTRL", "SHIFT", "ALT", "META", "TAB",
# "ESC", "BACKSPACE", "ARROWUP", etc. xdotool uses xkeysym names ("Return",
# "ctrl", "shift", "alt", "super", "Tab", "Escape", "BackSpace", "Up", ...).
_KEY_MAP = {
    "ENTER": "Return",
    "RETURN": "Return",
    "TAB": "Tab",
    "ESC": "Escape",
    "ESCAPE": "Escape",
    "BACKSPACE": "BackSpace",
    "DELETE": "Delete",
    "SPACE": "space",
    "ARROWUP": "Up",
    "ARROWDOWN": "Down",
    "ARROWLEFT": "Left",
    "ARROWRIGHT": "Right",
    "PAGEUP": "Page_Up",
    "PAGEDOWN": "Page_Down",
    "HOME": "Home",
    "END": "End",
    "CTRL": "ctrl",
    "CONTROL": "ctrl",
    "SHIFT": "shift",
    "ALT": "alt",
    "META": "super",
    "CMD": "super",
    "WIN": "super",
    "CAPSLOCK": "Caps_Lock",
}


def _translate_key(k: str) -> str:
    upper = k.upper()
    if upper in _KEY_MAP:
        return _KEY_MAP[upper]
    # Single character: xdotool can take it directly (e.g. "a", "1", "/").
    return k


def _press_keys(keys: list[str]) -> None:
    """Press a sequence of keys as a chord (joined by '+' for xdotool)."""
    if not keys:
        return
    chord = "+".join(_translate_key(k) for k in keys)
    _xdotool("key", "--", chord)


# --- Top-level dispatch ------------------------------------------------------

def execute_action(action: dict[str, Any]) -> dict[str, Any]:
    """Execute one OpenAI computer_call action.

    `action` is the ``action`` field of a Responses API computer_call item,
    e.g. ``{"type": "click", "x": 120, "y": 240, "button": "left"}``.

    All coordinates from the model are in MODEL_WIDTH x MODEL_HEIGHT space.
    Convert to real Xvfb pixel space at the boundary, before any mouse helper
    touches xdotool.

    Returns:
        {"screenshot_b64": str|None, "error": str|None}
    """
    typ = action.get("type", "")

    def _coord(x: int, y: int) -> tuple[int, int]:
        """Convert model-space coords to real Xvfb coords."""
        return _scale_to_real(int(x), int(y))

    try:
        if typ == "screenshot":
            _, b64 = take_screenshot()
            return {"screenshot_b64": b64, "error": None}

        if typ == "click":
            x, y = _coord(int(action["x"]), int(action["y"]))
            button = action.get("button", "left")
            _move(x, y)
            if button.lower() == "wheel":
                # OpenAI treats "wheel" as a click for some legacy reason; map to scroll-down by 1.
                _xdotool("click", "5")
            else:
                _click_button(button)

        elif typ == "double_click":
            x, y = _coord(int(action["x"]), int(action["y"]))
            _move(x, y)
            _xdotool("click", "--repeat", "2", "--delay", "80", "1")

        elif typ == "type":
            text = action.get("text", "")
            _xdotool("type", "--delay", "12", "--", text)

        elif typ == "keypress":
            keys = action.get("keys") or []
            _press_keys(list(keys))

        elif typ == "scroll":
            x, y = _coord(
                int(action.get("x", MODEL_WIDTH // 2)),
                int(action.get("y", MODEL_HEIGHT // 2))
            )
            sx = int(action.get("scroll_x", 0))
            sy = int(action.get("scroll_y", 0))
            _move(x, y)
            # Vertical scroll: positive sy = scroll DOWN (matches DOM convention).
            if sy != 0:
                button = "5" if sy > 0 else "4"
                _xdotool("click", "--repeat", str(max(1, abs(sy) // 100 or 1)),
                         "--delay", "30", button)
            # Horizontal scroll: positive sx = scroll RIGHT.
            if sx != 0:
                button = "7" if sx > 0 else "6"
                _xdotool("click", "--repeat", str(max(1, abs(sx) // 100 or 1)),
                         "--delay", "30", button)

        elif typ == "move":
            x, y = _coord(int(action["x"]), int(action["y"]))
            _move(x, y)

        elif typ == "drag":
            path = action.get("path") or []
            if len(path) >= 2:
                start = path[0]
                x, y = _coord(int(start["x"]), int(start["y"]))
                _move(x, y)
                _xdotool("mousedown", "1")
                for pt in path[1:]:
                    x, y = _coord(int(pt["x"]), int(pt["y"]))
                    _move(x, y)
                _xdotool("mouseup", "1")

        elif typ == "wait":
            time.sleep(float(action.get("ms", 1000)) / 1000.0)

        else:
            return {"screenshot_b64": None,
                    "error": f"unsupported action type: {typ!r}"}

        # After any state change, capture a fresh screenshot so the model
        # sees the result on its next turn.
        time.sleep(0.4)
        _, b64 = take_screenshot()
        return {"screenshot_b64": b64, "error": None}

    except subprocess.CalledProcessError as e:
        return {"screenshot_b64": None,
                "error": f"{typ} failed: {e.stderr.decode('utf-8', 'replace')[:500]}"}
    except Exception as e:
        return {"screenshot_b64": None, "error": f"{typ} failed: {e}"}
