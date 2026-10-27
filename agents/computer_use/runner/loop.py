"""Computer Use agent loop.

Drives one experimental session end-to-end:
    [system prompt + task]
         |
         v
    Claude -> tool_use (screenshot | click | type | ...)
         |             ^
         |             | tool_result (new screenshot)
         v             |
    execute via xdotool
         |
         v
    ... until Claude stops calling tools (stop_reason="end_turn")

Defaults to the 2025-11-24 tool spec for claude-sonnet-4-6, with a
model-specific fallback to the 2025-01-24 spec for claude-haiku-4-5.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from anthropic import Anthropic

from .tools import execute_action, take_screenshot, MODEL_WIDTH, MODEL_HEIGHT
from shared.prompts import SYSTEM_PROMPT, TIME_PRESSURE_SYSTEM_PROMPT_TEMPLATE  # noqa: E402
from shared.token_accounting import apply_token_record_to_result, usage_from_anthropic


# Tool/beta pinning. Keep the current Sonnet 4.6 AND Opus 4.8 path on the newest
# supported Computer Use beta (computer_20251124), but use the older beta for
# Haiku 4.5 because the provider rejects computer_20251124 for that model.
# Opus 4.8 (2026-06, strongest computer-use model) supports computer_20251124,
# so it intentionally falls through computer_use_spec_for_model()'s default
# branch with Sonnet — no separate prefix needed. Only Haiku is special-cased.
COMPUTER_TOOL_TYPE = "computer_20251124"
COMPUTER_USE_BETA = "computer-use-2025-11-24"
HAIKU_COMPUTER_TOOL_TYPE = "computer_20250124"
HAIKU_COMPUTER_USE_BETA = "computer-use-2025-01-24"
HAIKU_COMPUTER_USE_MODEL_PREFIXES = ("claude-haiku-4-5",)
ADAPTIVE_THINKING_MODEL_PREFIXES = (
    "claude-sonnet-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
)
THINKING_OFF_VALUES = {"", "0", "false", "no", "none", "off", "disabled"}
THINKING_DISPLAY_VALUES = {"summarized", "omitted"}
THINKING_EFFORT_VALUES = {"low", "medium", "high", "xhigh", "max"}

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 20000
DEFAULT_MAX_ITERATIONS = 80       # hard cap on API turns per session
DEFAULT_MAX_WALL_SECONDS = 30 * 60  # 30 min wall-clock cap
DEFAULT_THINKING_MODE = "auto"
DEFAULT_THINKING_BUDGET_TOKENS = 4096
REPEATED_ACTION_GUARD_LIMIT = 8
# Returned as the tool_result for any 2nd+ tool_use block the model batches into a
# single response. The action is NOT executed; this enforces one action per turn.
ONE_ACTION_PER_TURN_MESSAGE = (
    "Only one action per response is allowed in this experiment. This action was "
    "NOT executed. Take exactly one action per turn, then wait for the next "
    "screenshot before acting again."
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
    # Total wall time spent in inter-turn throttle sleeps (rate-limit
    # avoidance). NOT counted as decision time against time_limit_seconds.
    throttle_sleep_seconds: float = 0.0
    token_usage_records: list[dict[str, Any]] = field(default_factory=list)
    thinking_mode: str = DEFAULT_THINKING_MODE
    thinking_display: Optional[str] = None
    thinking_effort: Optional[str] = None
    thinking_budget_tokens: Optional[int] = None


def computer_use_spec_for_model(model: str) -> tuple[str, str]:
    """Return the Computer Use tool type and beta header for this model."""
    model_name = (model or "").lower()
    if any(model_name.startswith(prefix) for prefix in HAIKU_COMPUTER_USE_MODEL_PREFIXES):
        return HAIKU_COMPUTER_TOOL_TYPE, HAIKU_COMPUTER_USE_BETA
    return COMPUTER_TOOL_TYPE, COMPUTER_USE_BETA


def _supports_adaptive_thinking(model: str) -> bool:
    model_name = (model or "").lower()
    return any(model_name.startswith(prefix) for prefix in ADAPTIVE_THINKING_MODEL_PREFIXES)


def _normalize_optional(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip().lower()
    return text or None


def _thinking_request_for_model(
    *,
    model: str,
    mode: str,
    display: Optional[str],
    effort: Optional[str],
    budget_tokens: int,
    max_tokens: int,
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]], str, Optional[int]]:
    """Build Anthropic thinking/output_config payloads for an opt-in run."""
    requested = _normalize_optional(mode) or "off"
    if requested in THINKING_OFF_VALUES:
        return None, None, "off", None
    if requested not in {"auto", "adaptive", "manual"}:
        raise ValueError(
            "thinking_mode must be one of off, auto, adaptive, manual "
            f"(got {mode!r})"
        )

    display_value = _normalize_optional(display) or "summarized"
    if display_value not in THINKING_DISPLAY_VALUES:
        raise ValueError(
            "thinking_display must be summarized or omitted "
            f"(got {display!r})"
        )

    effort_value = _normalize_optional(effort)
    if effort_value and effort_value not in THINKING_EFFORT_VALUES:
        raise ValueError(
            "thinking_effort must be low, medium, high, xhigh, or max "
            f"(got {effort!r})"
        )

    adaptive_supported = _supports_adaptive_thinking(model)
    effective_mode = requested
    if requested == "auto":
        effective_mode = "adaptive" if adaptive_supported else "manual"
    if effective_mode == "adaptive" and not adaptive_supported:
        raise ValueError(
            f"{model} does not support adaptive thinking; use auto or manual"
        )
    if effective_mode == "manual" and adaptive_supported:
        raise ValueError(
            f"{model} uses adaptive thinking in this runner; use auto or adaptive"
        )

    if effective_mode == "adaptive":
        thinking = {"type": "adaptive", "display": display_value}
        output_config = {"effort": effort_value} if effort_value else None
        return thinking, output_config, effective_mode, None

    budget = int(budget_tokens)
    if budget < 1024:
        raise ValueError("manual thinking budget must be at least 1024 tokens")
    if budget >= max_tokens:
        raise ValueError("manual thinking budget must be smaller than max_tokens")
    thinking = {
        "type": "enabled",
        "budget_tokens": budget,
        "display": display_value,
    }
    return thinking, None, "manual", budget


def _drop_old_screenshots(messages):
    """Return a shallow-copied messages list with screenshots stripped from all
    but the most recent user message that contains one.
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


def _tool_result_content(result):
    blocks = []
    if result.get("error"):
        return [{"type": "text", "text": f"ERROR: {result['error']}"}]
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


def _action_signature(tool_input: dict[str, Any]) -> tuple[Any, ...]:
    coord = tool_input.get("coordinate")
    start_coord = tool_input.get("start_coordinate")
    return (
        tool_input.get("action"),
        tuple(coord) if isinstance(coord, list) else coord,
        tuple(start_coord) if isinstance(start_coord, list) else start_coord,
        tool_input.get("text"),
        tool_input.get("scroll_direction"),
        tool_input.get("scroll_amount"),
    )


def run_agent_session(
    *,
    task: str,
    model: str = DEFAULT_MODEL,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_wall_seconds: float = DEFAULT_MAX_WALL_SECONDS,
    time_limit_seconds: Optional[float] = None,
    turn_sleep_seconds: float = 0.0,
    thinking_mode: str = DEFAULT_THINKING_MODE,
    thinking_display: str = "summarized",
    thinking_effort: Optional[str] = None,
    thinking_budget_tokens: int = DEFAULT_THINKING_BUDGET_TOKENS,
    on_turn: Optional[Callable[[int, dict], None]] = None,
) -> SessionResult:
    """Run one Computer Use session.

    turn_sleep_seconds (default 0.0) - fixed wall-clock sleep inserted BEFORE
    every API call except the first. Used for rate-limit throttling. The
    sleep is accumulated in result.throttle_sleep_seconds and EXCLUDED from
    the time-pressure deadline elapsed.
    """
    computer_tool_type, computer_use_beta = computer_use_spec_for_model(model)
    client = Anthropic(default_headers={"anthropic-beta": computer_use_beta})

    _, initial_b64 = take_screenshot()
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": task},
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/png", "data": initial_b64,
            }},
        ],
    }]

    tools = [{
        "type": computer_tool_type,
        "name": "computer",
        "display_width_px": MODEL_WIDTH,
        "display_height_px": MODEL_HEIGHT,
        "display_number": int(os.environ.get("DISPLAY_NUM", "1")),
    }]

    max_tokens = DEFAULT_MAX_TOKENS
    thinking_payload, output_config, effective_thinking_mode, effective_budget = (
        _thinking_request_for_model(
            model=model,
            mode=thinking_mode,
            display=thinking_display,
            effort=thinking_effort,
            budget_tokens=thinking_budget_tokens,
            max_tokens=max_tokens,
        )
    )

    result = SessionResult(
        thinking_mode=effective_thinking_mode,
        thinking_display=(thinking_display if thinking_payload else None),
        thinking_effort=(thinking_effort if output_config else None),
        thinking_budget_tokens=effective_budget,
    )
    started = time.monotonic()
    completed_normally = False
    repeated_signature = None
    repeated_count = 0

    try:
        for turn in range(1, max_iterations + 1):
            # Inter-turn throttle: sleep BEFORE every API call except turn 1.
            if turn > 1 and turn_sleep_seconds > 0:
                time.sleep(turn_sleep_seconds)
                result.throttle_sleep_seconds += turn_sleep_seconds

            elapsed = time.monotonic() - started
            if elapsed > max_wall_seconds:
                result.error = f"wall-clock cap ({max_wall_seconds}s) exceeded"
                break

            # Decision-time elapsed excludes throttle sleep so the agent gets
            # its full nominal decision-time budget regardless of throttle.
            decision_elapsed = elapsed - result.throttle_sleep_seconds

            if time_limit_seconds is not None and decision_elapsed >= time_limit_seconds:
                result.stop_reason = "time_pressure_exceeded"
                completed_normally = True
                break

            if time_limit_seconds is None:
                system_prompt = SYSTEM_PROMPT
            else:
                remaining = max(0, int(round(time_limit_seconds - decision_elapsed)))
                system_prompt = TIME_PRESSURE_SYSTEM_PROMPT_TEMPLATE.format(
                    time_limit_seconds=int(time_limit_seconds),
                    remaining_seconds=remaining,
                )

            current_user_input = (
                messages[-1].get("content") if messages and messages[-1].get("role") == "user"
                else None
            )

            request_kwargs = {
                "model": model,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "tools": tools,
                "messages": _drop_old_screenshots(messages),
            }
            if thinking_payload:
                request_kwargs["thinking"] = thinking_payload
                if output_config:
                    request_kwargs["output_config"] = output_config

            response = client.messages.create(**request_kwargs)

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

            tool_results = []
            actions_log = []
            turn_signatures = []
            # --- ONE ACTION PER TURN (hard enforcement) ----------------------
            # The provider may return several tool_use blocks in one response
            # (Claude Sonnet 5 batches actions; older tiers emit one). Execute
            # ONLY the first so every model runs an identical 1-action-per-turn
            # loop. Each extra tool_use still gets a tool_result (the API requires
            # one reply per tool_use id) marked not-executed, and is NOT added to
            # actions_log, so actions.jsonl stays 1 line/turn.
            for idx, tu in enumerate(tool_uses):
                tool_input = tu.get("input") or {}
                if idx > 0:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tu.get("id"),
                        "content": [{"type": "text", "text": ONE_ACTION_PER_TURN_MESSAGE}],
                        "is_error": True,
                    })
                    continue
                exec_result = execute_action(tool_input)
                turn_signatures.append(_action_signature(tool_input))
                actions_log.append({
                    "tool_use_id": tu.get("id"),
                    "action": tool_input.get("action"),
                    "input": tool_input,
                    "computer_tool_type": computer_tool_type,
                    "computer_use_beta": computer_use_beta,
                    "had_error": bool(exec_result.get("error")),
                    "error": exec_result.get("error"),
                })
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
