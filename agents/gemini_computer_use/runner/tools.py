"""xdotool-based action dispatcher for Google's Gemini computer-use tool.

Gemini's Computer Use returns normalized 0-1000 coordinates that we convert to
1280x800 model space, then upscale to 1920x1200 real display pixels.

Screenshots are downscaled to MODEL_WIDTH x MODEL_HEIGHT before being sent to
the model (the model never sees the full 1920x1200 resolution). This keeps the
model's coordinate space consistent. Aspect ratio MUST be preserved:
1280:800 = 1920:1200 = 16:10.

Action names and signatures:
    open_web_browser()
    wait_5_seconds()
    go_back()
    go_forward()
    search()                                 # we ignore
    navigate(url: str)                       # we DECLINE
    click_at(x: int, y: int)
    hover_at(x: int, y: int)
    type_text_at(x: int, y: int, text: str, press_enter: bool = False, clear_before_typing: bool = True)
    key_combination(keys: list[str])
    scroll_document(direction: "up"|"down"|"left"|"right")
    scroll_at(x: int, y: int, direction: str, magnitude: int)
    drag_and_drop(x: int, y: int, destination_x: int, destination_y: int)
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
# Real Xvfb resolution. Must match docker-compose.yml WIDTH/HEIGHT.
WIDTH = int(os.environ.get("WIDTH", "1920"))
HEIGHT = int(os.environ.get("HEIGHT", "1200"))  # 16:10 to match MODEL_WIDTH:MODEL_HEIGHT

# Resolution the MODEL sees in screenshots and uses for coordinates.
# Same aspect-ratio invariant as ANTHCU: 1280:800 = 1920:1200 = 16:10.
MODEL_WIDTH = int(os.environ.get("MODEL_WIDTH", "1280"))
MODEL_HEIGHT = int(os.environ.get("MODEL_HEIGHT", "800"))

# CRITICAL invariant: aspect ratios must match (enforced as in ANTHCU).
if WIDTH * MODEL_HEIGHT != HEIGHT * MODEL_WIDTH:
    raise RuntimeError(
        f"Aspect-ratio mismatch: real {WIDTH}x{HEIGHT} "
        f"vs model {MODEL_WIDTH}x{MODEL_HEIGHT}. "
        f"They MUST share aspect ratio (currently {WIDTH/HEIGHT:.4f} vs "
        f"{MODEL_WIDTH/MODEL_HEIGHT:.4f}). "
        f"Fix WIDTH/HEIGHT in docker-compose.yml or env vars."
    )

SCREENSHOT_DIR = Path("/tmp/screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


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


# --- Coordinate scaling (0-1000 normalized → real pixels) -------------------

def _scale_to_real(x: int, y: int) -> tuple[int, int]:
    """Convert normalized 0-1000 Gemini coordinates to real Xvfb pixel coordinates.

    The conversion is: normalize(0-1000) → MODEL_WIDTH/HEIGHT → real WIDTH/HEIGHT
    For 0-1000, we first scale to model space (0-MODEL_WIDTH/HEIGHT) then to real.
    """
    # Normalize 0-1000 → 0-MODEL_WIDTH/HEIGHT (Gemini's model output space)
    model_x = int(round(x * MODEL_WIDTH / 1000))
    model_y = int(round(y * MODEL_HEIGHT / 1000))
    # Model space → real pixels
    return (
        int(round(model_x * WIDTH / MODEL_WIDTH)),
        int(round(model_y * HEIGHT / MODEL_HEIGHT)),
    )


def _scale_to_model(x: int, y: int) -> tuple[int, int]:
    """Inverse: real pixel coords → model space (for cursor_position if needed)."""
    return (
        int(round(x * MODEL_WIDTH / WIDTH)),
        int(round(y * MODEL_HEIGHT / HEIGHT)),
    )


# --- Screenshot --------------------------------------------------------------

def take_screenshot() -> tuple[bytes, str]:
    """Capture Xvfb display, downscale to model space, and return base64.

    Two PNGs are written to /tmp/screenshots/:
        screen_{ts}.png         — real-resolution (for trace_logger / noVNC)
        screen_{ts}_model.png   — downscaled to MODEL_WIDTH x MODEL_HEIGHT
                                  (sent to the model)

    Returns (model_png_bytes, model_base64_str). The base64 is what gets
    embedded in the API request — the model sees the downscaled screenshot
    so its coordinate space aligns with the 0-1000 normalization.
    """
    ts = int(time.time() * 1000)
    real_path = SCREENSHOT_DIR / f"screen_{ts}.png"
    _run(["scrot", "-o", str(real_path)])
    raw = real_path.read_bytes()

    # Downscale to model space so the model's outgoing 0-1000 coordinates align
    # with what _scale_to_real() expects on the way back.
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


# --- Mouse / keyboard primitives ---------------------------------------------

# Settle delay after every cursor move. We do NOT use xdotool's `--sync` flag
# because it waits for an X MotionNotify event that never fires when moving to
# the same coordinate. This happens often when the agent retries and causes a
# 10s timeout. Plain mousemove + a tiny sleep is both faster and immune to
# no-op-move hang. Follow-up clicks are guaranteed FIFO after the move.
_MOVE_SETTLE_SEC = 0.05


def _move(x: int, y: int) -> None:
    _xdotool("mousemove", str(x), str(y))
    time.sleep(_MOVE_SETTLE_SEC)


def _click(x: int, y: int) -> None:
    _move(x, y)
    _xdotool("click", "1")


_KEY_MAP = {
    "ENTER": "Return", "RETURN": "Return",
    "TAB": "Tab", "ESC": "Escape", "ESCAPE": "Escape",
    "BACKSPACE": "BackSpace", "DELETE": "Delete",
    "SPACE": "space",
    "ARROWUP": "Up", "ARROWDOWN": "Down",
    "ARROWLEFT": "Left", "ARROWRIGHT": "Right",
    "UP": "Up", "DOWN": "Down", "LEFT": "Left", "RIGHT": "Right",
    "PAGEUP": "Page_Up", "PAGEDOWN": "Page_Down",
    "HOME": "Home", "END": "End",
    "CTRL": "ctrl", "CONTROL": "ctrl",
    "SHIFT": "shift", "ALT": "alt",
    "META": "super", "CMD": "super", "WIN": "super",
}


def _translate(k: str) -> str:
    upper = k.upper()
    return _KEY_MAP.get(upper, k)


def _press_combo(keys: list[str]) -> None:
    if not keys:
        return
    chord = "+".join(_translate(k) for k in keys)
    _xdotool("key", "--", chord)


_SCROLL_BTN = {"up": "4", "down": "5", "left": "6", "right": "7"}


def _scroll_at(x: int, y: int, direction: str, magnitude: int = 3) -> None:
    btn = _SCROLL_BTN.get(direction.lower(), "5")
    _move(x, y)
    _xdotool("click", "--repeat", str(max(1, magnitude)), "--delay", "30", btn)


# --- Top-level dispatch ------------------------------------------------------

def execute_action(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Execute one Gemini computer_use function call.

    `name` is the function name; `args` is the parsed argument dict.
    Returns: {"text": str|None, "screenshot_b64": str|None, "error": str|None}
    """
    try:
        if name in ("open_web_browser",):
            # Browser is already open at the experiment URL. No-op.
            pass

        elif name in ("wait_5_seconds", "wait"):
            time.sleep(float(args.get("seconds", 5.0)))

        elif name == "go_back":
            _xdotool("key", "alt+Left")

        elif name == "go_forward":
            _xdotool("key", "alt+Right")

        elif name in ("navigate", "search"):
            # Decline navigation — agent must stay on the experiment page.
            return {
                "text": "Navigation is disabled in this task. Stay on the current page.",
                "screenshot_b64": take_screenshot()[1],
                "error": None,
            }

        elif name in ("click_at", "click"):
            x, y = _scale_to_real(int(args["x"]), int(args["y"]))
            _click(x, y)

        elif name in ("hover_at", "hover", "move"):
            x, y = _scale_to_real(int(args["x"]), int(args["y"]))
            _move(x, y)

        elif name in ("type_text_at", "type_text", "type"):
            text = args.get("text", "")
            x = args.get("x")
            y = args.get("y")
            if x is not None and y is not None:
                xi, yi = _scale_to_real(int(x), int(y))
                _click(xi, yi)
            if args.get("clear_before_typing", True):
                _xdotool("key", "ctrl+a")
                _xdotool("key", "Delete")
            _xdotool("type", "--delay", "12", "--", text)
            if args.get("press_enter", False):
                _xdotool("key", "Return")

        elif name == "key_combination":
            keys = args.get("keys") or []
            _press_combo(list(keys))

        elif name == "scroll_document":
            direction = args.get("direction", "down")
            x, y = WIDTH // 2, HEIGHT // 2
            _scroll_at(x, y, direction, magnitude=3)

        elif name == "scroll_at":
            x, y = _scale_to_real(int(args["x"]), int(args["y"]))
            direction = args.get("direction", "down")
            magnitude = int(args.get("magnitude", 3))
            _scroll_at(x, y, direction, magnitude=magnitude)

        elif name == "drag_and_drop":
            x1, y1 = _scale_to_real(int(args["x"]), int(args["y"]))
            x2, y2 = _scale_to_real(int(args["destination_x"]), int(args["destination_y"]))
            _move(x1, y1)
            _xdotool("mousedown", "1")
            _move(x2, y2)
            _xdotool("mouseup", "1")

        elif name in ("take_screenshot", "screenshot"):
            pass  # screenshot is taken below

        else:
            return {"text": None, "screenshot_b64": None,
                    "error": f"unsupported function: {name!r}"}

        time.sleep(0.4)
        _, b64 = take_screenshot()
        return {"text": None, "screenshot_b64": b64, "error": None}

    except subprocess.CalledProcessError as e:
        return {"text": None, "screenshot_b64": None,
                "error": f"{name} failed: {e.stderr.decode('utf-8', 'replace')[:500]}"}
    except Exception as e:
        return {"text": None, "screenshot_b64": None, "error": f"{name} failed: {e}"}
