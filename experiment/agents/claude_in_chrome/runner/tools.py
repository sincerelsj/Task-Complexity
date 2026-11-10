"""Custom tool definitions + dispatcher for the Claude-in-Chrome runner.

Unlike the Anthropic CU runner (which uses the official ``computer_20250124``
tool spec), this runner exposes a curated set of CUSTOM tools that mirror
what a Chrome extension agent (i.e. Claude-in-Chrome) would offer. This makes
the execution path CDP-based — every action goes through Playwright, which
dispatches via Chrome DevTools Protocol.

Tools:
    take_screenshot()                      → returns fresh PNG
    click_at(x, y)                         → primary click at viewport coords
    right_click_at(x, y)
    double_click_at(x, y)
    type_text(text)                        → into focused element
    press_key(key)                         → e.g. "Enter", "Tab", "Escape"
    scroll(direction, amount, x?, y?)      → wheel scroll at viewport center or (x,y)
    wait(seconds)

Coordinates are absolute viewport pixels in MODEL space (MODEL_WIDTH x
MODEL_HEIGHT, default 1280x800), matching what Claude sees in the screenshot.
browser.py upscales to real Xvfb pixels (WIDTH x HEIGHT) before dispatching
to Playwright — see error_log #07 for why this matters.
"""

from __future__ import annotations

import time
from typing import Any

from .browser import Browser, MODEL_WIDTH, MODEL_HEIGHT


# Tool schema for the Anthropic Messages API. Each tool definition lives
# here so the loop can pass them in the API call without duplication.
TOOL_DEFINITIONS = [
    {
        "name": "take_screenshot",
        "description": "Capture a fresh PNG screenshot of the current browser viewport.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "click_at",
        "description": (
            "Left-click at the given viewport pixel coordinates "
            f"(0 ≤ x < {MODEL_WIDTH}, 0 ≤ y < {MODEL_HEIGHT}). The viewport "
            "matches what you see in the most recent screenshot."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "double_click_at",
        "description": "Double-click at the given viewport pixel coordinates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "right_click_at",
        "description": "Right-click at the given viewport pixel coordinates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "type_text",
        "description": (
            "Type the given literal text into whichever element currently has "
            "focus. Click first if you need to focus a specific input."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "press_key",
        "description": (
            "Press a single named key or chord. Use Playwright key names: "
            "'Enter', 'Tab', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', "
            "'ArrowRight', 'Backspace', 'Control+A', 'Meta+C', etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    },
    {
        "name": "scroll",
        "description": (
            "Wheel-scroll the page. direction: 'up' | 'down' | 'left' | 'right'. "
            "amount: positive integer (rough page-units). Optional x,y pin the "
            "scroll origin; omit to scroll at the current viewport center."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string",
                              "enum": ["up", "down", "left", "right"]},
                "amount": {"type": "integer", "minimum": 1},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
            },
            "required": ["direction", "amount"],
        },
    },
    {
        "name": "wait",
        "description": "Pause for the given number of seconds (max 10).",
        "input_schema": {
            "type": "object",
            "properties": {"seconds": {"type": "number", "minimum": 0,
                                        "maximum": 10}},
            "required": ["seconds"],
        },
    },
]


def execute_tool(browser: Browser, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Execute one tool call. Returns {"text", "screenshot_b64", "error"}.

    Always attaches a fresh screenshot after state-changing actions so the
    next API turn sees the result.
    """
    try:
        if name == "take_screenshot":
            _, b64 = browser.screenshot()
            return {"text": None, "screenshot_b64": b64, "error": None}

        if name == "click_at":
            browser.click(int(args["x"]), int(args["y"]))
        elif name == "double_click_at":
            browser.double_click(int(args["x"]), int(args["y"]))
        elif name == "right_click_at":
            browser.click(int(args["x"]), int(args["y"]), button="right")
        elif name == "type_text":
            browser.type_text(str(args.get("text", "")))
        elif name == "press_key":
            browser.press_key(str(args.get("key", "")))
        elif name == "scroll":
            direction = str(args.get("direction", "down")).lower()
            amount = max(1, int(args.get("amount", 3)))
            x = int(args.get("x", MODEL_WIDTH // 2))
            y = int(args.get("y", MODEL_HEIGHT // 2))
            # ~100px per "amount" unit feels close to a page-unit scroll.
            step = 100 * amount
            if direction == "down":
                browser.scroll(x, y, 0, step)
            elif direction == "up":
                browser.scroll(x, y, 0, -step)
            elif direction == "right":
                browser.scroll(x, y, step, 0)
            elif direction == "left":
                browser.scroll(x, y, -step, 0)
        elif name == "wait":
            time.sleep(float(args.get("seconds", 1.0)))
        else:
            return {"text": None, "screenshot_b64": None,
                    "error": f"unknown tool: {name!r}"}

        time.sleep(0.4)
        _, b64 = browser.screenshot()
        return {"text": None, "screenshot_b64": b64, "error": None}

    except Exception as e:
        return {"text": None, "screenshot_b64": None, "error": f"{name} failed: {e}"}
