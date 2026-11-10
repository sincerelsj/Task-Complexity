"""OpenAI Computer Use agent loop.

Drives one experimental session against the OpenAI Responses API.

2026-05-27 — GPT-5.5 GA migration:
  Tool definition:
    GA (gpt-5.4-mini):            {"type": "computer"}                       # no dimensions/environment
    Legacy (computer-use-preview): {"type": "computer_use_preview",
                                    "display_width": ...,
                                    "display_height": ...,
                                    "environment": "browser"}
  Response action shape:
    GA may batch:   computer_call.actions[]  # runner executes one action only
    Legacy single:  computer_call.action
  Follow-up screenshot (computer_call_output.output):
    GA:     {"type": "computer_screenshot", "image_url": ..., "detail": "original"}
    Legacy: {"type": "input_image",         "image_url": ...}

Selector: ``COMPUTER_TOOL_TYPE`` env var (``OAICU_TOOL_TYPE``); default
``"computer"`` (GA). Set to ``"computer_use_preview"`` for legacy path until
the preview model is shut down on 2026-07-23.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from openai import OpenAI

from .tools import execute_action, take_screenshot, WIDTH, HEIGHT, MODEL_WIDTH, MODEL_HEIGHT
from shared.prompts import SYSTEM_PROMPT, TIME_PRESSURE_SYSTEM_PROMPT_TEMPLATE
from shared.token_accounting import apply_token_record_to_result, usage_from_openai

COMPUTER_TOOL_TYPE = os.environ.get("OAICU_TOOL_TYPE", "computer")
DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_MAX_TOKENS = 20000
DEFAULT_TEMPERATURE = 0.0
DEFAULT_REASONING_EFFORT = os.environ.get("OAICU_REASONING_EFFORT", "medium")
DEFAULT_MAX_ITERATIONS = 80
DEFAULT_MAX_WALL_SECONDS = 30 * 60
MAX_ACTIONS_PER_SCREENSHOT = 1
# Behavioral-loop guard: abort after this many consecutive identical executed
# actions (same parity rule as ANTHCU/CLDCR loops, 2026-06-11).
REPEATED_ACTION_GUARD_LIMIT = 8


def _action_signature(action: dict) -> tuple:
    """Stable identity of an executed OpenAI CU action for the repeated-action
    guard. Includes coordinates/text so distinct clicks are NOT conflated
    (lesson from CLDCR false positive, docs/07 #31)."""
    a = action or {}
    return (
        a.get("type"),
        a.get("x"),
        a.get("y"),
        a.get("button"),
        a.get("text"),
        a.get("keys") if isinstance(a.get("keys"), str) else tuple(a.get("keys") or ()),
        a.get("scroll_x"),
        a.get("scroll_y"),
        tuple(tuple(p.values()) for p in (a.get("path") or []) if isinstance(p, dict)),
    )


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
    throttle_sleep_seconds: float = 0.0
    token_usage_records: list[dict[str, Any]] = field(default_factory=list)


def _extract_text(items):
    parts = []
    for it in items:
        if it.get("type") == "message":
            for c in it.get("content") or []:
                if isinstance(c, dict) and c.get("type") in (
                    "output_text", "text", "input_text",
                ):
                    parts.append(c.get("text") or "")
    return "\n".join(p for p in parts if p)


def _as_dict(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return {"_raw": str(obj)}


def _drop_old_screenshots(messages):
    import copy
    img_indices = [
        i for i, msg in enumerate(messages)
        if msg.get("role") == "user"
        and isinstance(msg.get("content"), list)
        and any(
            isinstance(b, dict) and b.get("type") == "input_image"
            for b in msg["content"]
        )
    ]
    if len(img_indices) <= 1:
        return messages
    result = copy.deepcopy(messages)
    for idx in img_indices[:-1]:
        stripped = [
            b for b in result[idx]["content"]
            if not (isinstance(b, dict) and b.get("type") == "input_image")
        ]
        result[idx]["content"] = stripped or [{"type": "input_text", "text": "(screenshot removed)"}]
    return result


def _build_tools_payload(tool_type: str):
    """GA computer-tool models reject display_width/display_height/environment;
    legacy preview requires them."""
    if tool_type == "computer":
        return [{"type": "computer"}]
    # Legacy preview path.
    return [{
        "type": tool_type,
        "display_width": MODEL_WIDTH,
        "display_height": MODEL_HEIGHT,
        "environment": "browser",
    }]


def _build_screenshot_output(tool_type: str, screenshot_b64: Optional[str]) -> dict:
    """GA uses computer_screenshot (+ detail); legacy uses input_image."""
    image_url = (
        f"data:image/png;base64,{screenshot_b64}"
        if screenshot_b64 else
        "data:image/png;base64,"
    )
    if tool_type == "computer":
        return {
            "type": "computer_screenshot",
            "image_url": image_url,
            "detail": "original",
        }
    return {
        "type": "input_image",
        "image_url": image_url,
    }


def _extract_call_actions(call: dict) -> list[dict]:
    """Return the actions requested by a computer_call in wire order."""
    raw_actions = call.get("actions")
    if raw_actions:
        return [a for a in raw_actions if a]
    single = call.get("action")
    return [single] if single else []


def run_agent_session(
    *,
    task: str,
    model: str = DEFAULT_MODEL,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_wall_seconds: float = DEFAULT_MAX_WALL_SECONDS,
    time_limit_seconds: Optional[float] = None,
    turn_sleep_seconds: float = 0.0,
    on_turn: Optional[Callable[[int, dict], None]] = None,
) -> SessionResult:
    """Run one OpenAI Computer Use session."""
    client = OpenAI()

    tools = _build_tools_payload(COMPUTER_TOOL_TYPE)

    _, initial_b64 = take_screenshot()
    initial_input = [{
        "role": "user",
        "content": [
            {"type": "input_text", "text": task},
            {"type": "input_image",
             "image_url": f"data:image/png;base64,{initial_b64}"},
        ],
    }]

    result = SessionResult()
    started = time.monotonic()
    previous_response_id = None
    next_input = initial_input
    completed_normally = False
    repeated_signature = None
    repeated_count = 0

    try:
        for turn in range(1, max_iterations + 1):
            if turn > 1 and turn_sleep_seconds > 0:
                time.sleep(turn_sleep_seconds)
                result.throttle_sleep_seconds += turn_sleep_seconds

            elapsed = time.monotonic() - started
            if elapsed > max_wall_seconds:
                result.error = f"wall-clock cap ({max_wall_seconds}s) exceeded"
                break

            decision_elapsed = elapsed - result.throttle_sleep_seconds

            if time_limit_seconds is not None and decision_elapsed >= time_limit_seconds:
                result.stop_reason = "time_pressure_exceeded"
                completed_normally = True
                break

            kwargs = {
                "model": model,
                "input": _drop_old_screenshots(next_input),
                "tools": tools,
                "truncation": "auto",
                "max_output_tokens": DEFAULT_MAX_TOKENS,
            }

            _is_reasoning = (
                model.startswith("gpt-5")
                or model.startswith("o1")
                or model.startswith("o3")
                or model.startswith("o4")
            )
            if _is_reasoning:
                kwargs["reasoning"] = {"effort": DEFAULT_REASONING_EFFORT}
            else:
                kwargs["temperature"] = DEFAULT_TEMPERATURE

            if time_limit_seconds is None:
                system_prompt = SYSTEM_PROMPT
            else:
                remaining = max(0, int(round(time_limit_seconds - decision_elapsed)))
                system_prompt = TIME_PRESSURE_SYSTEM_PROMPT_TEMPLATE.format(
                    time_limit_seconds=int(time_limit_seconds),
                    remaining_seconds=remaining,
                )
            kwargs["instructions"] = system_prompt
            if previous_response_id is not None:
                kwargs["previous_response_id"] = previous_response_id

            current_user_input = next_input[-1] if next_input else None

            response = client.responses.create(**kwargs)
            previous_response_id = response.id

            usage = getattr(response, "usage", None)
            if usage is not None:
                usage_record = usage_from_openai(
                    usage,
                    model=model,
                    turn=turn,
                    call_index=len(result.token_usage_records) + 1,
                )
                result.token_usage_records.append(usage_record)
                apply_token_record_to_result(result, usage_record)

            output_items = [_as_dict(o) for o in (response.output or [])]
            result.iterations = turn
            result.stop_reason = getattr(response, "status", None) or "ok"

            text = _extract_text(output_items)
            if text:
                result.final_text = text

            calls = [it for it in output_items if it.get("type") == "computer_call"]

            if not calls:
                if on_turn:
                    on_turn(turn, {
                        "system_prompt": system_prompt,
                        "user_input": current_user_input,
                        "assistant": output_items,
                        "actions": [],
                        "stop_reason": result.stop_reason,
                    })
                completed_normally = True
                break

            actions_log = []
            selected_call = None
            selected_action = None
            skipped_action_count = 0
            remaining_action_budget = MAX_ACTIONS_PER_SCREENSHOT
            for call in calls:
                actions = _extract_call_actions(call)
                for action in actions:
                    if remaining_action_budget > 0 and selected_action is None:
                        selected_call = call
                        selected_action = action
                        remaining_action_budget -= 1
                    else:
                        skipped_action_count += 1

            if selected_call is None or selected_action is None:
                result.error = "computer_call returned no executable action"
                if on_turn:
                    on_turn(turn, {
                        "system_prompt": system_prompt,
                        "user_input": current_user_input,
                        "assistant": output_items,
                        "actions": [],
                        "stop_reason": result.stop_reason,
                    })
                break

            # One screenshot should authorize exactly one UI action. GPT-5.5 may
            # batch actions in computer_call.actions[], but executing a batch
            # means later clicks are based on stale pixels. Execute only the
            # first requested action, then send a fresh screenshot back.
            exec_result = execute_action(selected_action or {})
            actions_log.append({
                "tool_use_id": selected_call.get("call_id") or selected_call.get("id"),
                "action": (selected_action or {}).get("type"),
                "input": selected_action,
                "had_error": bool(exec_result.get("error")),
                "error": exec_result.get("error"),
                "skipped_batched_actions": skipped_action_count,
            })

            call_output = {
                "type": "computer_call_output",
                "call_id": selected_call.get("call_id") or selected_call.get("id"),
                "output": _build_screenshot_output(
                    COMPUTER_TOOL_TYPE,
                    exec_result.get("screenshot_b64"),
                ),
            }
            psc = selected_call.get("pending_safety_checks") or []
            if psc:
                call_output["acknowledged_safety_checks"] = psc

            next_input = [call_output]

            if on_turn:
                on_turn(turn, {
                    "system_prompt": system_prompt,
                    "user_input": current_user_input,
                    "assistant": output_items,
                    "actions": actions_log,
                    "stop_reason": result.stop_reason,
                })

            # Repeated identical action guard (parity with ANTHCU/CLDCR).
            if not actions_log[0].get("had_error"):
                sig = _action_signature(selected_action)
                if sig == repeated_signature:
                    repeated_count += 1
                else:
                    repeated_signature = sig
                    repeated_count = 1
                if repeated_count >= REPEATED_ACTION_GUARD_LIMIT:
                    result.stop_reason = "repeated_action_guard"
                    result.error = (
                        f"repeated identical action {repeated_count} times: "
                        f"{(selected_action or {}).get('type')} {selected_action}"
                    )
                    break

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
