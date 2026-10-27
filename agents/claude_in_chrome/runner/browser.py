"""Playwright-managed Chromium controller for the Claude-in-Chrome runner.

Owns:
    - launching Chromium (headed, on the Xvfb display so VNC can show it)
    - taking screenshots via CDP, downscaled to MODEL_WIDTH x MODEL_HEIGHT
    - synthesizing input via CDP, with model-space → real-space coordinate
      scaling

This is the architectural difference from the Anthropic CU runner: actions
are dispatched via the Chrome DevTools Protocol rather than X11 events. The
result looks identical to the experiment page (which only sees DOM-level
events) but matches the execution model of a browser extension agent like
Claude-in-Chrome.

**Coordinate space** (mirrors error_log #07 fix in ANTHCU):
The Playwright viewport runs at WIDTH x HEIGHT (real). Claude is trained
to output coordinates in ~1280x800 space; if we send a 1920x1200 screenshot
and trust the raw click coordinate, every click is shifted ~(1.5, 1.5)x
toward the top-left. So we downscale the screenshot to MODEL_WIDTH x
MODEL_HEIGHT before showing the model, and upscale every click/move coord
back to real before handing it to Playwright. The aspect ratios of real and
model spaces MUST match (1280:800 = 1920:1200 = 16:10), enforced at import
with a RuntimeError.
"""

from __future__ import annotations

import base64
import io
import os
import time
from pathlib import Path
from typing import Optional

from PIL import Image
from playwright.sync_api import sync_playwright, BrowserContext, Page, Playwright


# --- Coordinate spaces -------------------------------------------------------

# Real viewport resolution (also the Xvfb display size; the Chromium window
# is sized to match in start()).
WIDTH = int(os.environ.get("WIDTH", "1920"))
HEIGHT = int(os.environ.get("HEIGHT", "1200"))  # 16:10 to match MODEL_*

# Resolution Claude sees in screenshots and uses for click coordinates.
# Claude is trained at <=1280x800; sending higher-res screenshots and trusting
# the returned coords causes systematic top-left misses. See error_log #07.
MODEL_WIDTH = int(os.environ.get("MODEL_WIDTH", "1280"))
MODEL_HEIGHT = int(os.environ.get("MODEL_HEIGHT", "800"))

# CRITICAL invariant: aspect ratios must match.
# If they don't, downscaling distorts non-uniformly and (x,y) scale factors
# differ, meaning a click on the visual center of a cell in the downscaled
# screenshot does NOT inverse-map to the real cell center.
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
    """Convert MODEL-space (MODEL_WIDTH x MODEL_HEIGHT) coords → real viewport
    pixel coords (WIDTH x HEIGHT) for Playwright dispatch."""
    return (
        int(round(x * WIDTH / MODEL_WIDTH)),
        int(round(y * HEIGHT / MODEL_HEIGHT)),
    )


def _scale_to_model(x: int, y: int) -> tuple[int, int]:
    """Inverse of _scale_to_real. Used when surfacing pixel positions back to
    the model so it can reuse them directly."""
    return (
        int(round(x * MODEL_WIDTH / WIDTH)),
        int(round(y * MODEL_HEIGHT / HEIGHT)),
    )


class Browser:
    """Owns the playwright lifecycle for one session.

    All public mouse methods take MODEL-space coordinates; scaling to real
    viewport pixels happens internally before dispatch.
    """

    def __init__(self) -> None:
        self._pw: Optional[Playwright] = None
        self._browser = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    def start(self, url: str) -> None:
        self._pw = sync_playwright().start()
        # Use system Chromium (installed via apt in the base image) so we
        # don't have to download Playwright's bundled build at run time.
        # Headed mode: Xvfb provides the display; VNC streams it to the user.
        chromium_path = "/usr/bin/chromium"
        launch_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            f"--window-size={WIDTH},{HEIGHT}",
            "--window-position=0,0",
        ]
        self._browser = self._pw.chromium.launch(
            executable_path=chromium_path,
            headless=False,
            args=launch_args,
        )
        self._context = self._browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            locale="ko-KR",
        )
        self._page = self._context.new_page()
        self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        # Extra settle so dynamic JS ('tracker.js') initializes.
        time.sleep(1.5)

    def stop(self) -> None:
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    @property
    def page(self) -> Page:
        assert self._page is not None, "Browser.start() must be called first"
        return self._page

    # --- Actions (CDP-level) -----------------------------------------------
    def screenshot(self) -> tuple[bytes, str]:
        """PNG screenshot of the current viewport, downscaled to MODEL space.

        Two files are written to /tmp/screenshots/ for debugging:
            screen_{ts}.png         — native (WIDTH x HEIGHT), what noVNC viewers see
            screen_{ts}_model.png   — downscaled (MODEL_WIDTH x MODEL_HEIGHT), what the model sees

        Returns (model_png_bytes, model_base64_str). The base64 is what gets
        embedded in the API request — the model NEVER sees the native-resolution
        capture so its coord output is in MODEL_WIDTH x MODEL_HEIGHT space.
        """
        ts = int(time.time() * 1000)
        native_path = SCREENSHOT_DIR / f"screen_{ts}.png"
        model_path = SCREENSHOT_DIR / f"screen_{ts}_model.png"

        # Native capture (preserved for trace_logger / human review).
        native_bytes = self.page.screenshot(full_page=False, type="png")
        native_path.write_bytes(native_bytes)

        # Downscale to MODEL space with LANCZOS (high-quality resampling).
        with Image.open(io.BytesIO(native_bytes)) as im:
            if im.size != (MODEL_WIDTH, MODEL_HEIGHT):
                im = im.resize((MODEL_WIDTH, MODEL_HEIGHT), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            model_bytes = buf.getvalue()
        model_path.write_bytes(model_bytes)

        return model_bytes, base64.standard_b64encode(model_bytes).decode("ascii")

    def click(self, x: int, y: int, button: str = "left",
              click_count: int = 1) -> None:
        rx, ry = _scale_to_real(x, y)
        self.page.mouse.click(rx, ry, button=button, click_count=click_count)

    def double_click(self, x: int, y: int) -> None:
        rx, ry = _scale_to_real(x, y)
        self.page.mouse.dblclick(rx, ry)

    def move(self, x: int, y: int) -> None:
        rx, ry = _scale_to_real(x, y)
        self.page.mouse.move(rx, ry)

    def type_text(self, text: str) -> None:
        self.page.keyboard.type(text, delay=12)

    def press_key(self, key: str) -> None:
        # Playwright keys: "Enter", "Tab", "Escape", "ArrowDown", "Control+A", ...
        self.page.keyboard.press(key)

    def scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        rx, ry = _scale_to_real(x, y)
        # dx/dy are wheel deltas (page-units / pixels), independent of the
        # coord space, so we don't scale them.
        self.page.mouse.move(rx, ry)
        self.page.mouse.wheel(dx, dy)

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        rx1, ry1 = _scale_to_real(x1, y1)
        rx2, ry2 = _scale_to_real(x2, y2)
        self.page.mouse.move(rx1, ry1)
        self.page.mouse.down()
        self.page.mouse.move(rx2, ry2, steps=10)
        self.page.mouse.up()
