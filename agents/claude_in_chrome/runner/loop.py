"""Claude-in-Chrome agent loop.

Mirrors the structure of the Anthropic Computer Use loop, but:
    - tools are CUSTOM (browser-extension-shaped) instead of the official
      ``computer_20250124`` tool spec
    - actions execute via Playwright/CDP instead of xdotool/X11

This makes the runner architecturally equivalent to a Chrome extension
agent: the same Anthropic model + same task, but its clicks happen at the
DOM level rather than as kernel-synthesized X events.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from anthropic import Anthropic

from .browser import Browser
from .tools import TOOL_DEFINITIONS, execute_tool
from shared.prompts import SYSTEM_PROMPT as _BASE_PROMPT, TOOL_SECTION_HEADER, TIME_PRESSURE_SYSTEM_PROMPT_TEMPLATE
from shared.token_accounting import apply_token_record_to_result, usage_from_anthropic


DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_MAX_TOKENS = 20000
DEFAULT_MAX_ITERATIONS = 80
DEFAULT_MAX_WALL_SECONDS = 30 * 60
MAX_ACTIONS_PER_SCREENSHOT = 1
REPEATED_ACTION_GUARD_LIMIT = 8


# CDP-specific tool guidance (appended to base prompt + tool section header).
_CDP_TOOL_GUIDANCE = """\
You drive the browser by emitting tool calls (click_at, type_text, press_key,
scroll, take_screenshot, wait). Coordinates in your tool calls are absolute
pixels in the viewport you see in the most recent screenshot.
"""

# Static system prompt for sessions without time pressure.
SYSTEM_PROMPT = _BASE_PROMPT + TOOL_SECTION_HEADER + _CDP_TOOL_GUIDANCE


@dataclass
class SessionResult:
    iterations: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    stop_reason: Optional[str] = None
    wall_seconds: float = 0.0
    final_text: str = ""
    error: Optional[str] = None
    # Total wall time spent in inter-turn throttle sleeps (rate-limit
    # avoidance). NOT counted against time_limit_seconds deadline.
    throttle_sleep_seconds: float = 0.0
    token_usage_records: list[dict[str, Any]] = field(default_factory=list)


def _drop_old_screenshots(messages):
    """Return a shallow-copied messages list with screenshots stripped from all
    but the most recent user message that contains one.

    Identical to ANTHCU's _drop_old_screenshots (anthropic image block shape,
    including nested tool_result.content[] image blocks).
    Added 2026-05-27 to unify input context length with ANTHCU baseline:
    without this, CLDCR was accumulating every screenshot across turns while
    ANTHCU/OAICU/GEMCU kept only the latest.
    """
    import copy

    def has_image(block):
        if isinstance(block, dict):
            if block.get("type") == "image":
                return True
            return any(has_image(v) for v in block.values())
        if isinstance(block, list):
            return any(has_image(item) for item in block)
        return False

    def strip_images(block):
        if isinstance(block, list):
            stripped = []
            for item in block:
                cleaned = strip_images(item)
                if cleaned is not None:
                    stripped.append(cleaned)
            return stripped
        if not isinstance(block, dict):
            return block
        if block.get("type") == "image":
            return None

        cleaned = {}
        for key, value in block.items():
            cleaned_value = strip_images(value)
            if cleaned_value is not None:
                cleaned[key] = cleaned_value
        if block.get("type") == "tool_result" and not cleaned.get("content"):
            cleaned["content"] = [{"type": "text", "text": "(screenshot removed)"}]
        return cleaned

    img_indices = [
        i for i, msg in enumerate(messages)
        if msg["role"] == "user"
        and has_image(msg.get("content"))
    ]
    if len(img_indices) <= 1:
        return messages
    result = copy.deepcopy(messages)
    for idx in img_indices[:-1]:
        stripped = strip_images(result[idx]["content"])
        result[idx]["content"] = stripped or [{"type": "text", "text": "(screenshot removed)"}]
    return result


def _tool_result_content(result: dict[str, Any]) -> list[dict[str, Any]]:
    if result.get("error"):
        return [{"type": "text", "text": f"ERROR: {result['error']}"}]
    blocks: list[dict[str, Any]] = []
    if result.get("text"):
        blocks.append({"type": "text", "text": result["text"]})
    if result.get("screenshot_b64"):
        blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": result["screenshot_b64"],
            },
        })
    return blocks or [{"type": "text", "text": "(no output)"}]


def _action_signature(action: str, tool_input: dict[str, Any]) -> tuple[Any, ...]:
    coord = tool_input.get("coordinate")
    start_coord = tool_input.get("start_coordinate")
    return (
        action,
        tool_input.get("x"),
        tool_input.get("y"),
        tuple(coord) if isinstance(coord, list) else coord,
        tuple(start_coord) if isinstance(start_coord, list) else start_coord,
        tool_input.get("text"),
        tool_input.get("selector"),
        tool_input.get("scroll_direction"),
        tool_input.get("scroll_amount"),
    )


def run_agent_session(
    *,
    url: str,
    task: str,
    model: str = DEFAULT_MODEL,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_wall_seconds: float = DEFAULT_MAX_WALL_SECONDS,
    time_limit_seconds: Optional[float] = None,
    turn_sleep_seconds: float = 0.0,
    on_turn: Optional[Callable[[int, dict], None]] = None,
) -> SessionResult:
    """Run one Claude-in-Chrome session.

    Unlike the pixel-level runners, this one launches Chromium itself (via
    Playwright). The base entrypoint should be invoked with
    ``SKIP_CHROMIUM_AUTO_OPEN=1`` so we don't end up with two browsers.

    ``time_limit_seconds`` (optional) — if set, the session runs in TIME
    PRESSURE mode: the system prompt is reformatted before each API call to
    show remaining time, and the loop exits without making another API call
    once remaining time hits zero. Independent from ``max_wall_seconds`` (which
    is a hard safety cap, much larger).

    ``turn_sleep_seconds`` (default 0.0) — fixed wall-clock sleep inserted
    BEFORE every API call except the first. Used for rate-limit throttling;
    accumulated in ``result.throttle_sleep_seconds`` and EXCLUDED from the
    time-pressure deadline elapsed.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return SessionResult(error="ANTHROPIC_API_KEY not set")

    browser = Browser()
    result = SessionResult()
    started = time.monotonic()
    completed_normally = False
    repeated_signature = None
    repeated_count = 0

    try:
        browser.start(url)
        client = Anthropic()

        _, initial_b64 = browser.screenshot()
        messages: list[dict[str, Any]] = [{
            "role": "user",
            "content": [
                {"type": "text", "text": task},
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/png", "data": initial_b64,
                }},
            ],
        }]

        for turn in range(1, max_iterations + 1):
            # Inter-turn throttle: sleep BEFORE every API call except turn 1.
            # Tracked separately so time-pressure decision_elapsed can subtract
            # it — agent's announced deadline is preserved.
            if turn > 1 and turn_sleep_seconds > 0:
                time.sleep(turn_sleep_seconds)
                result.throttle_sleep_seconds += turn_sleep_seconds

            elapsed = time.monotonic() - started
            if elapsed > max_wall_seconds:
                result.error = f"wall-clock cap ({max_wall_seconds}s) exceeded"
                break

            decision_elapsed = elapsed - result.throttle_sleep_seconds

            # Time-pressure soft deadline: stop BEFORE making another API call
            # once the model's announced budget is exhausted.
            if time_limit_seconds is not None and decision_elapsed >= time_limit_seconds:
                result.stop_reason = "time_pressure_exceeded"
                completed_normally = True
                break

            # System prompt: static for the no-deadline default, freshly
            # formatted with remaining time for the time-pressure variant.
            if time_limit_seconds is None:
                system_prompt = SYSTEM_PROMPT
            else:
                remaining = max(0, int(round(time_limit_seconds - decision_elapsed)))
                system_prompt = TIME_PRESSURE_SYSTEM_PROMPT_TEMPLATE.format(
                    time_limit_seconds=int(time_limit_seconds),
                    remaining_seconds=remaining,
                ) + TOOL_SECTION_HEADER + _CDP_TOOL_GUIDANCE

            # Snapshot the last user input (last message's content if role == user).
            current_user_input = (
                messages[-1].get("content") if messages and messages[-1].get("role") == "user"
                else None
            )

            response = client.messages.create(
                model=model,
                max_tokens=DEFAULT_MAX_TOKENS,
                temperature=0.0,
                system=system_prompt,
                tools=TOOL_DEFINITIONS,
                tool_choice={"type": "auto", "disable_parallel_tool_use": True},
                messages=_drop_old_screenshots(messages),
            )

            result.iterations = turn
            usage_record = usage_from_anthropic(
                response.usage,
                model=model,
                turn=turn,
                call_index=len(result.token_usage_records) + 1,
            )
            result.token_usage_records.append(usage_record)
            apply_token_record_to_result(result, usage_record)
            result.stop_reason = response.stop_reason

            assistant_content = [b.model_dump() for b in response.content]
            messages.append({"role": "assistant", "content": assistant_content})

            text_parts = [b["text"] for b in assistant_content if b.get("type") == "text"]
            if text_parts:
                result.final_text = "\n".join(text_parts)

            tool_uses = [b for b in assistant_content if b.get("type") == "tool_use"]
            if not tool_uses:
                if on_turn:
                    on_turn(turn, {
                        "system_prompt": system_prompt,
                        "user_input": current_user_input,
                        "assistant": assistant_content,
                        "actions": [],
                        "stop_reason": response.stop_reason,
                    })
                completed_normally = True
                break


            tool_results: list[dict[str, Any]] = []
            actions_log: list[dict[str, Any]] = []
            turn_signatures: list[tuple[Any, ...]] = []
            skipped_batched_actions = max(len(tool_uses) - MAX_ACTIONS_PER_SCREENSHOT, 0)
            for idx, tu in enumerate(tool_uses):
                name = tu.get("name") or ""
                tool_input = tu.get("input") or {}
                if idx < MAX_ACTIONS_PER_SCREENSHOT:
                    exec_result = execute_tool(browser, name, tool_input)
                    turn_signatures.append(_action_signature(name, tool_input))
                    actions_log.append({
                        "tool_use_id": tu.get("id"),
                        "action": name,
                        "input": tool_input,
                        "had_error": bool(exec_result.get("error")),
                        "error": exec_result.get("error"),
                        "skipped_batched_actions": skipped_batched_actions,
                    })
                else:
                    exec_result = {
                        "text": (
                            "SKIPPED: one-action-per-screenshot gate executed "
                            "only the first tool call from this model response. "
                            "Use the fresh screenshot before requesting another action."
                        ),
                    }
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.get("id"),
                    "content": _tool_result_content(exec_result),
                    "is_error": bool(exec_result.get("error")),
                })

            messages.append({"role": "user", "content": tool_results})

            if on_turn:
                on_turn(turn, {
                    "system_prompt": system_prompt,
                    "user_input": current_user_input,
                    "assistant": assistant_content,
                    "actions": actions_log,
                    "stop_reason": response.stop_reason,
                })

            if len(turn_signatures) == 1 and not actions_log[0].get("had_error"):
                if turn_signatures[0] == repeated_signature:
                    repeated_count += 1
                else:
                    repeated_signature = turn_signatures[0]
                    repeated_count = 1
                if repeated_count >= REPEATED_ACTION_GUARD_LIMIT:
                    result.stop_reason = "repeated_action_guard"
                    result.error = (
                        f"repeated identical action {repeated_count} times: "
                        f"{actions_log[0].get('action')} "
                        f"{actions_log[0].get('input')}"
                    )
                    break
            else:
                repeated_signature = None
                repeated_count = 0

    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"

    if (
        result.error is None
        and not completed_normally
        and result.iterations >= max_iterations
    ):
        result.error = (
            f"max iterations ({max_iterations}) reached before model stopped "
            "calling tools"
        )

    result.wall_seconds = time.monotonic() - started
    return result
