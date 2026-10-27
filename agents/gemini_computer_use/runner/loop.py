"""Gemini Computer Use agent loop.

2026-05-27: migrated default model from `gemini-2.5-computer-use-preview-05-2025`
(2026-10-16 deprecation) to `gemini-3-flash-preview`. Gemini 3 unifies
computer use into the general model — no separate `*-computer-use-preview-*`
slug is needed. Tool spec stays `Tool(computer_use=ComputerUse(...))`.

To run with Pro instead of Flash, override at the CLI or via env var:
    --agent-model gemini-3-pro-preview
    $env:GEMCU_MODEL = "gemini-3-pro-preview"  # picked up by batch scripts

Avoid `gemini-3.5-flash` — its model page explicitly lists computer use as
UNSUPPORTED. Stay on `gemini-3-flash-preview` for CU workloads.

Auth modes:
    - Gemini Developer API: GEMINI_API_KEY or GOOGLE_API_KEY.
    - Vertex AI: GOOGLE_GENAI_USE_VERTEXAI=true plus GOOGLE_CLOUD_PROJECT and
      GOOGLE_CLOUD_LOCATION. Credentials are supplied through ADC, for example
      GOOGLE_APPLICATION_CREDENTIALS=/workspace/secrets/<service-account>.json.
"""

from __future__ import annotations

import os
import time
import base64
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from google import genai
from google.genai import types as genai_types

from .tools import execute_action, take_screenshot, MODEL_WIDTH, MODEL_HEIGHT
from shared.prompts import SYSTEM_PROMPT, TIME_PRESSURE_SYSTEM_PROMPT_TEMPLATE  # noqa: E402
from shared.token_accounting import apply_token_record_to_result, usage_from_gemini

# 2026-05-27: migrated 2.5-cu-preview → 3-flash-preview (Pro via override).
DEFAULT_MODEL = "gemini-3-flash-preview"
DEFAULT_MAX_TOKENS = 20000  # 2026-05-27 unified with ANTHCU/CLDCR
DEFAULT_TEMPERATURE = 0.0   # 2026-05-27 unified across 6 frameworks
DEFAULT_MAX_ITERATIONS = 80
DEFAULT_MAX_WALL_SECONDS = 30 * 60
# Behavioral-loop guard: abort after this many consecutive identical executed
# actions (parity with ANTHCU/CLDCR/OAICU, 2026-06-11).
REPEATED_ACTION_GUARD_LIMIT = 8


def _action_signature(name: str, args: dict) -> tuple:
    """Stable identity of an executed Gemini CU action for the repeated-action
    guard. Coordinates/text included so distinct clicks are not conflated."""
    a = args or {}
    return (
        name,
        a.get("x"),
        a.get("y"),
        a.get("destination_x"),
        a.get("destination_y"),
        a.get("text"),
        a.get("keys") if isinstance(a.get("keys"), str) else tuple(a.get("keys") or ()),
        a.get("direction"),
        a.get("scroll_amount"),
    )


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


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


def _build_genai_client() -> tuple[Optional[genai.Client], Optional[str]]:
    """Build a Google Gen AI client without printing or returning secrets."""
    if _truthy(os.environ.get("GOOGLE_GENAI_USE_VERTEXAI")):
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        location = os.environ.get("GOOGLE_CLOUD_LOCATION")
        if not project or not location:
            return None, (
                "GOOGLE_GENAI_USE_VERTEXAI=true requires GOOGLE_CLOUD_PROJECT "
                "and GOOGLE_CLOUD_LOCATION"
            )
        return genai.Client(
            vertexai=True,
            project=project,
            location=location,
        ), None

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return None, (
            "Set GEMINI_API_KEY/GOOGLE_API_KEY for Gemini Developer API, or set "
            "GOOGLE_GENAI_USE_VERTEXAI=true with GOOGLE_CLOUD_PROJECT and "
            "GOOGLE_CLOUD_LOCATION for Vertex AI"
        )
    return genai.Client(api_key=api_key), None


def _drop_old_screenshots(contents):
    import copy

    def has_screenshot(part) -> bool:
        if getattr(part, "inline_data", None) is not None:
            return True
        fr = getattr(part, "function_response", None)
        response = getattr(fr, "response", None)
        if isinstance(response, dict) and response.get("screenshot"):
            return True
        return any(
            getattr(fr_part, "inline_data", None) is not None
            for fr_part in (getattr(fr, "parts", None) or [])
        )

    def strip_screenshot(part):
        if getattr(part, "inline_data", None) is not None:
            return None
        fr = getattr(part, "function_response", None)
        response = getattr(fr, "response", None)
        if isinstance(response, dict) and response.get("screenshot"):
            stripped = dict(response)
            stripped.pop("screenshot", None)
            if not stripped:
                stripped["text"] = "(screenshot removed)"
            try:
                fr.response = stripped
            except Exception:
                return genai_types.Part(text="(screenshot removed)")
        if getattr(fr, "parts", None):
            try:
                fr.parts = []
            except Exception:
                pass
        return part

    screenshot_indices = [
        i for i, content in enumerate(contents)
        if content.role == "user"
        and any(
            has_screenshot(p)
            for p in (content.parts or [])
        )
    ]
    if len(screenshot_indices) <= 1:
        return contents
    result = copy.deepcopy(contents)
    for idx in screenshot_indices[:-1]:
        stripped = []
        for part in result[idx].parts or []:
            part = strip_screenshot(part)
            if part is not None:
                stripped.append(part)
        if not stripped:
            stripped = [genai_types.Part(text="(screenshot removed)")]
        result[idx].parts = stripped
    return result


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    return str(value)


def _part_to_dict(part):
    return _json_safe(part)


def _content_to_log(content):
    if content is None:
        return None
    return _json_safe(content)


def _build_initial_contents(task, initial_png):
    return [genai_types.Content(
        role="user",
        parts=[
            genai_types.Part(text=task),
            genai_types.Part(inline_data=genai_types.Blob(
                mime_type="image/png",
                data=initial_png,
            )),
        ],
    )]


def _function_response_part(name, call_id, screenshot_b64, text, error):
    payload = {}
    if error:
        payload["error"] = error
    if text:
        payload["text"] = text
    kwargs = {"name": name, "response": payload}
    if screenshot_b64:
        kwargs["parts"] = [genai_types.FunctionResponsePart(
            inline_data=genai_types.FunctionResponseBlob(
                mime_type="image/png",
                data=base64.standard_b64decode(screenshot_b64),
            )
        )]
    fr = genai_types.FunctionResponse(**kwargs)
    if call_id:
        try:
            fr.id = call_id
        except Exception:
            pass
    return genai_types.Part(function_response=fr)


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
    """Run one Gemini Computer Use session.

    turn_sleep_seconds (default 0.0) - rate-limit throttle inserted before
    every API call except the first. Excluded from time-pressure deadline.
    """
    client, client_error = _build_genai_client()
    if client_error or client is None:
        return SessionResult(error=client_error or "failed to initialize Gemini client")

    tool = genai_types.Tool(computer_use=genai_types.ComputerUse(
        environment=genai_types.Environment.ENVIRONMENT_BROWSER,
    ))
    base_config = {"tools": [tool]}

    initial_png, _ = take_screenshot()
    contents = _build_initial_contents(task, initial_png)

    result = SessionResult()
    started = time.monotonic()
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

            if time_limit_seconds is None:
                system_prompt = SYSTEM_PROMPT
            else:
                remaining = max(0, int(round(time_limit_seconds - decision_elapsed)))
                system_prompt = TIME_PRESSURE_SYSTEM_PROMPT_TEMPLATE.format(
                    time_limit_seconds=int(time_limit_seconds),
                    remaining_seconds=remaining,
                )

            config = genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=DEFAULT_TEMPERATURE,
                max_output_tokens=DEFAULT_MAX_TOKENS,
                **base_config
            )

            pruned_contents = _drop_old_screenshots(contents)

            current_user_input = (
                _content_to_log(contents[-1])
                if contents and contents[-1].role == "user"
                else None
            )

            response = client.models.generate_content(
                model=model,
                contents=pruned_contents,
                config=config,
            )

            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                usage_record = usage_from_gemini(
                    usage,
                    model=model,
                    turn=turn,
                    call_index=len(result.token_usage_records) + 1,
                )
                result.token_usage_records.append(usage_record)
                apply_token_record_to_result(result, usage_record)

            candidates = response.candidates or []
            if not candidates:
                result.error = "no candidates returned"
                break

            cand = candidates[0]
            content = cand.content
            parts = list(content.parts or [])
            result.iterations = turn
            result.stop_reason = getattr(cand, "finish_reason", None)
            if hasattr(result.stop_reason, "name"):
                result.stop_reason = result.stop_reason.name

            text_parts = [p.text for p in parts if getattr(p, "text", None)]
            if text_parts:
                result.final_text = "\n".join(t for t in text_parts if t)

            fc_parts = [p for p in parts if getattr(p, "function_call", None)]

            assistant_log = [_part_to_dict(p) for p in parts]

            if not fc_parts:
                if on_turn:
                    on_turn(turn, {
                        "system_prompt": system_prompt,
                        "user_input": current_user_input,
                        "assistant": assistant_log,
                        "actions": [],
                        "stop_reason": result.stop_reason,
                    })
                completed_normally = True
                break

            contents.append(content)

            response_parts = []
            actions_log = []
            for p in fc_parts:
                fc = p.function_call
                name = getattr(fc, "name", "") or ""
                args = getattr(fc, "args", None) or {}
                if hasattr(args, "items"):
                    args = dict(args)
                exec_result = execute_action(name, args)
                actions_log.append({
                    "tool_use_id": getattr(fc, "id", None),
                    "action": name,
                    "input": args,
                    "had_error": bool(exec_result.get("error")),
                    "error": exec_result.get("error"),
                })
                response_parts.append(_function_response_part(
                    name=name,
                    call_id=getattr(fc, "id", None),
                    screenshot_b64=exec_result.get("screenshot_b64"),
                    text=exec_result.get("text"),
                    error=exec_result.get("error"),
                ))

            contents.append(genai_types.Content(role="user", parts=response_parts))

            if on_turn:
                on_turn(turn, {
                    "system_prompt": system_prompt,
                    "user_input": current_user_input,
                    "assistant": assistant_log,
                    "actions": actions_log,
                    "stop_reason": result.stop_reason,
                })

            # Repeated identical action guard (parity with ANTHCU/CLDCR/OAICU).
            # Only track turns that executed exactly one error-free action, so
            # batched turns are not conflated (same rule as CLDCR).
            if len(actions_log) == 1 and not actions_log[0].get("had_error"):
                sig = _action_signature(actions_log[0].get("action") or "",
                                        actions_log[0].get("input") or {})
                if sig == repeated_signature:
                    repeated_count += 1
                else:
                    repeated_signature = sig
                    repeated_count = 1
                if repeated_count >= REPEATED_ACTION_GUARD_LIMIT:
                    result.stop_reason = "repeated_action_guard"
                    result.error = (
                        f"repeated identical action {repeated_count} times: "
                        f"{actions_log[0].get('action')} {actions_log[0].get('input')}"
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
