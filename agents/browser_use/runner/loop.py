"""browser-use agent loop.

browser-use is a high-level Python library that pairs a vision-capable LLM
with Playwright. It runs its own observation/action loop internally (vision
+ extracted DOM as the model's input, structured browser actions as the
output), so this runner is mostly a configuration shim:

    1. configure the LLM (Anthropic by default; LangChain LLMs accepted)
    2. configure the browser to launch headed on Xvfb (so VNC can show it)
    3. run agent.run() with a max-steps cap
    4. translate `agent.history` into our trace_logger format

The Layer-1 behavioral signal still flows naturally — every click in
browser-use's controller hits the page, which fires tracker.js → /log /submit
on the host Flask app.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import os
import time
from dataclasses import dataclass, field
from itertools import zip_longest
from pathlib import Path
from typing import Any, Callable, Optional

from shared.prompts import (  # noqa: E402
    PROMPT_VERSION,
    SYSTEM_PROMPT as _BASE_SYSTEM_PROMPT,
    TIME_PRESSURE_SYSTEM_PROMPT_TEMPLATE,
)
from shared.token_accounting import apply_token_records_to_result, build_langchain_usage_handler


DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_MAX_ITERATIONS = 80
DEFAULT_MAX_WALL_SECONDS = 30 * 60
# Behavioral-loop guard threshold (parity with the other frameworks,
# 2026-06-11). browser-use's internal loop is not interruptible per step from
# this wrapper, so the guard is applied post-run on the executed action
# history and marks the run as failed.
REPEATED_ACTION_GUARD_LIMIT = 8


def _browse_action_signature(act: Any) -> Optional[tuple]:
    """Stable identity of one executed browser-use action.

    browser-use model_actions rows look like {action_name: params_dict, ...}
    plus bookkeeping keys. Returns None for rows we cannot identify."""
    if not isinstance(act, dict):
        return None
    for key, val in act.items():
        if key in ("interacted_element", "screenshot_path"):
            continue
        if isinstance(val, dict):
            try:
                params = tuple(sorted(
                    (k, str(v)) for k, v in val.items()
                    if k not in ("interacted_element",)
                ))
            except Exception:
                params = (str(val),)
            return (key, params)
    return None


_BROWSER_USE_TASK_SUFFIX = """\
The experiment page is already open at this URL: {url}
"""


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
    # avoidance). For library-internal loops we count via callback invocations.
    throttle_sleep_seconds: float = 0.0
    token_usage_records: list[dict[str, Any]] = field(default_factory=list)


class _ThrottleCallback:
    """LangChain AsyncCallbackHandler that sleeps before every LLM call.

    Library-internal loops (browser-use, LangChain AgentExecutor) don't expose
    a per-turn hook in our outer code, so we throttle via the LLM's own
    callback chain instead. Works for both chat models (Anthropic/OpenAI
    ChatModel) via ``on_chat_model_start`` and completion-style models via
    ``on_llm_start``.
    """

    def __init__(self, sleep_seconds: float, time_limit_seconds: Optional[float] = None):
        self.sleep_seconds = sleep_seconds
        self.time_limit_seconds = time_limit_seconds
        self.total_sleep = 0.0
        self.call_count = 0
        self.started_at: Optional[float] = None

    def start_timer(self) -> None:
        self.started_at = time.monotonic()

    def decision_elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        return time.monotonic() - self.started_at - self.total_sleep

    async def _sleep(self):
        if (self.time_limit_seconds is not None
                and self.decision_elapsed() >= self.time_limit_seconds):
            raise asyncio.TimeoutError(
                f"time_pressure_exceeded: deadline {int(self.time_limit_seconds)}s"
            )
        self.call_count += 1
        if self.call_count == 1:
            return
        if self.sleep_seconds > 0:
            await asyncio.sleep(self.sleep_seconds)
            self.total_sleep += self.sleep_seconds

    async def on_chat_model_start(self, *args, **kwargs):
        await self._sleep()

    async def on_llm_start(self, *args, **kwargs):
        await self._sleep()


def _build_async_throttle_handler(
    sleep_seconds: float,
    time_limit_seconds: Optional[float] = None,
):
    """Return an AsyncCallbackHandler subclass instance, or None if sleep is 0.

    Uses langchain_core (newer) with fallback to langchain.callbacks.base.
    Returns None when throttle is off to skip callback registration entirely.
    """
    if sleep_seconds <= 0:
        return None
    try:
        from langchain_core.callbacks import AsyncCallbackHandler  # type: ignore
    except Exception:
        from langchain.callbacks.base import AsyncCallbackHandler  # type: ignore

    # Build the actual subclass at import time so it inherits from whichever
    # AsyncCallbackHandler is available in the installed LangChain version.
    class _Handler(AsyncCallbackHandler, _ThrottleCallback):  # type: ignore[misc]
        def __init__(self, sleep_seconds: float, time_limit_seconds: Optional[float] = None):
            AsyncCallbackHandler.__init__(self)
            _ThrottleCallback.__init__(self, sleep_seconds, time_limit_seconds)

        async def on_chat_model_start(self, *args, **kwargs):
            await _ThrottleCallback.on_chat_model_start(self, *args, **kwargs)

        async def on_llm_start(self, *args, **kwargs):
            await _ThrottleCallback.on_llm_start(self, *args, **kwargs)

    return _Handler(sleep_seconds, time_limit_seconds)


def _write_browser_use_screenshot(
    screenshot_b64: Any,
    screenshot_dir: Path,
    step_number: int,
) -> Optional[str]:
    """Persist a browser-use state screenshot for trace logging only."""
    if not screenshot_b64:
        return None
    if isinstance(screenshot_b64, bytes):
        raw = screenshot_b64.decode("ascii", errors="ignore")
    else:
        raw = str(screenshot_b64)
    if "," in raw and raw.lstrip().startswith("data:image"):
        raw = raw.split(",", 1)[1]
    raw = raw.strip()
    if not raw:
        return None
    try:
        png = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError):
        return None
    try:
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = screenshot_dir / f"browser_use_step_{step_number:03d}.png"
        path.write_bytes(png)
        return str(path)
    except Exception:
        return None


def _history_actions_with_screenshots(history: Any, screenshot_dir: Path) -> list[tuple[dict, Optional[str]]]:
    """Flatten browser-use history while keeping each step screenshot."""
    pairs: list[tuple[dict, Optional[str]]] = []
    for step_number, item in enumerate(getattr(history, "history", []) or [], start=1):
        model_output = getattr(item, "model_output", None)
        state = getattr(item, "state", None)
        actions = getattr(model_output, "action", None) or []
        interacted = getattr(state, "interacted_element", None) or []
        screenshot_path = _write_browser_use_screenshot(
            getattr(state, "screenshot", None),
            screenshot_dir,
            step_number,
        )
        for action, interacted_element in zip_longest(actions, interacted):
            if action is None:
                continue
            if hasattr(action, "model_dump"):
                record = action.model_dump(exclude_none=True)
            elif isinstance(action, dict):
                record = dict(action)
            else:
                record = {"action": repr(action)}
            if interacted_element is not None and hasattr(interacted_element, "to_dict"):
                record["interacted_element"] = interacted_element.to_dict()
            else:
                record["interacted_element"] = interacted_element
            pairs.append((record, screenshot_path))
    return pairs


def _build_llm(model: str, callbacks=None):
    """Return a LangChain LLM instance for the requested model.

    We default to Anthropic (claude-*); fall back to OpenAI for gpt-* names.

    Hyperparameters unified across all 6 frameworks (2026-05-27):
      temperature=0.0, max_tokens=20000.
    """
    cb = list(callbacks) if callbacks else []
    if model.startswith("gpt-") or model.startswith("o"):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, temperature=0.0, max_tokens=20000, callbacks=cb)
    if model.startswith("gemini-"):
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model, temperature=0.0, max_output_tokens=20000, callbacks=cb)
    # Default: Anthropic.
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(model_name=model, temperature=0.0, max_tokens=20000, callbacks=cb)


async def _run_async(
    *,
    url: str,
    task: str,
    model: str,
    max_iterations: int,
    time_limit_seconds: Optional[float] = None,
    turn_sleep_seconds: float = 0.0,
    on_turn: Optional[Callable[[int, dict], None]] = None,
    trace_messages_path: Optional[Any] = None,
) -> SessionResult:
    # Imports are deferred so the runner file can be `py_compile`d without the
    # heavyweight browser-use install present.
    from browser_use import Agent  # type: ignore[import-not-found]
    try:
        from browser_use import Browser, BrowserConfig  # type: ignore
    except Exception:
        # Newer versions consolidate names differently.
        Browser = None  # type: ignore
        BrowserConfig = None  # type: ignore

    throttle_handler = _build_async_throttle_handler(
        turn_sleep_seconds,
        time_limit_seconds if time_limit_seconds and time_limit_seconds > 0 else None,
    )
    usage_handler = build_langchain_usage_handler(model)
    callbacks = [h for h in (throttle_handler, usage_handler) if h is not None]
    callbacks = callbacks or None
    llm = _build_llm(model, callbacks=callbacks)

    full_task = f"{task}\n\n{_BROWSER_USE_TASK_SUFFIX.format(url=url)}"

    # If the installed version exposes Browser/BrowserConfig, prefer headed
    # mode using the system Chromium. Otherwise let browser-use default.
    #
    # `extend_system_message`: append _shared/prompts.py's SYSTEM_PROMPT (or
    # the time-pressure variant) into the LLM's system prompt so the
    # experimental rules are visible in the system role, not just the task.
    # We use `extend` rather than `override` because browser-use's default
    # system prompt also contains essential library mechanics (JSON output
    # format, tool schema) — overriding breaks the loop. Added 2026-05-27 to
    # narrow the input-asymmetry gap vs ANTHCU baseline. Some browser-use
    # versions may not accept this kwarg; we try/except gracefully.
    extend_msg = (
        TIME_PRESSURE_SYSTEM_PROMPT_TEMPLATE.format(
            time_limit_seconds=int(time_limit_seconds),
            remaining_seconds=int(time_limit_seconds),
        )
        if (time_limit_seconds is not None and time_limit_seconds > 0)
        else _BASE_SYSTEM_PROMPT
    )

    agent_kwargs: dict[str, Any] = {
        "task": full_task,
        "llm": llm,
        "use_vision": True,
        "extend_system_message": extend_msg,
        "max_actions_per_step": 1,
    }
    if Browser is not None and BrowserConfig is not None:
        try:
            agent_kwargs["browser"] = Browser(config=BrowserConfig(
                headless=False,
                browser_binary_path="/usr/bin/chromium",
                extra_browser_args=[
                    "--no-sandbox",
                    "--disable-gpu-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--no-xshm",
                    "--window-size=1920,1200",
                ],
            ))
        except TypeError:
            # Older/newer versions have shifted these field names. Fall back
            # to the library default rather than failing import-time checks.
            try:
                agent_kwargs["browser"] = Browser(config=BrowserConfig(
                    headless=False,
                    executable_path="/usr/bin/chromium",
                ))
            except Exception:
                pass

    try:
        agent = Agent(**agent_kwargs)
    except TypeError as te:
        # Older browser-use versions may not accept `extend_system_message`.
        # Fall back without it; task body still carries the guardrails.
        if "extend_system_message" in str(te):
            agent_kwargs.pop("extend_system_message", None)
            agent = Agent(**agent_kwargs)
        else:
            raise

    # Log session initialization: system prompt + full task to messages.jsonl.
    # browser-use wraps its internal loop; we cannot capture per-turn system prompts,
    # so log the initial state once at session start.
    if trace_messages_path is not None:
        import json
        init_entry = {
            "turn": 0,
            "ts_ms": int(time.time() * 1000),
            "kind": "session_init",
            "prompt_version": PROMPT_VERSION,
            "system_prompt": extend_msg,
            "extend_system_message": extend_msg,    # _shared SYSTEM_PROMPT injected as Agent system kwarg (2026-05-27)
            "user_input": full_task,                # full task sent to agent.run()
            "library": "browser-use",
            "note": "Library-internal loop; per-turn API messages not visible to outer code. extend_system_message injects _shared/prompts.py SYSTEM_PROMPT into the library system prompt — overriding fully would break the library's tool-output contract.",
        }
        try:
            with open(trace_messages_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(init_entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    result = SessionResult()
    started = time.monotonic()
    if throttle_handler is not None:
        throttle_handler.start_timer()

    try:
        # For time-pressure, wrap agent.run() in a hard timeout so it terminates
        # cleanly if the deadline expires. Add a small buffer (5s) to allow the
        # model a graceful exit, but force termination if it overshoots.
        # If a throttle is active, extend the timeout to give the agent the
        # full nominal decision-time budget — every LLM call after the first
        # adds turn_sleep_seconds via the callback, so worst case adds
        # (max_iterations - 1) * turn_sleep_seconds wall time.
        if time_limit_seconds is not None and time_limit_seconds > 0:
            throttle_budget = max(max_iterations - 1, 0) * max(turn_sleep_seconds, 0.0)
            timeout = min(time_limit_seconds + throttle_budget + 5, 1800)  # hard safety cap
            try:
                history = await asyncio.wait_for(
                    agent.run(max_steps=max_iterations),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                result.error = f"time_pressure_exceeded: deadline {int(time_limit_seconds)}s"
                result.stop_reason = "time_pressure_exceeded"
                result.wall_seconds = time.monotonic() - started
                if throttle_handler is not None:
                    result.throttle_sleep_seconds = throttle_handler.total_sleep
                result.token_usage_records = list(getattr(usage_handler, "records", []) or [])
                apply_token_records_to_result(result, result.token_usage_records)
                return result
        else:
            history = await agent.run(max_steps=max_iterations)

        result.wall_seconds = time.monotonic() - started

        # browser-use returns an AgentHistoryList. Prefer its structured
        # history so each action row can reference the screenshot that the
        # model saw for that step.
        screenshot_dir = Path("/tmp/browser_use_trace_screenshots") / str(int(time.time() * 1000))
        try:
            action_pairs = _history_actions_with_screenshots(history, screenshot_dir)
        except Exception:
            action_pairs = []
        if action_pairs:
            actions = [act for act, _ in action_pairs]
        else:
            try:
                actions = history.model_actions() if hasattr(history, "model_actions") else []
            except Exception:
                actions = []
            action_pairs = [(act, None) for act in actions]
        try:
            result.final_text = history.final_result() or ""
        except Exception:
            result.final_text = ""

        # Iterations from action count (closest analogue to LLM turn count).
        result.iterations = len(actions) if actions else 0

        if result.iterations == 0 and not result.final_text:
            result.error = "browser-use completed without actions or final result"
            result.stop_reason = "failed"

        result.token_usage_records = list(getattr(usage_handler, "records", []) or [])
        apply_token_records_to_result(result, result.token_usage_records)

        # Repeated identical action guard (post-run; parity with the direct
        # API loops, 2026-06-11). browser-use's internal loop cannot be
        # interrupted per-step from the wrapper, so detect the behavioral
        # loop from the executed action history and mark the run failed.
        if result.stop_reason is None and actions:
            streak_sig, streak = None, 0
            for act in actions:
                sig = _browse_action_signature(act)
                if sig is not None and sig == streak_sig:
                    streak += 1
                else:
                    streak_sig, streak = sig, 1
                if streak >= REPEATED_ACTION_GUARD_LIMIT:
                    result.stop_reason = "repeated_action_guard"
                    result.error = (
                        f"repeated identical action {streak} times: {streak_sig}"
                    )
                    break

        # Completion semantics (parity with direct loops): a run that exhausts
        # max_steps without the model calling done must be a failure, not
        # "completed".
        history_done = None
        try:
            history_done = bool(history.is_done())
        except Exception:
            history_done = None

        # stop_reason: if not already set by the timeout/guard branches above,
        # mark completion only when browser-use actually reported done.
        if result.stop_reason is None:
            decision_elapsed = result.wall_seconds
            if throttle_handler is not None:
                decision_elapsed -= throttle_handler.total_sleep
            if (time_limit_seconds is not None
                    and decision_elapsed >= time_limit_seconds):
                result.stop_reason = "time_pressure_exceeded"
            elif history_done is False and result.iterations >= max_iterations:
                result.stop_reason = "max_iterations"
                result.error = (
                    f"max steps ({max_iterations}) reached without done"
                )
            else:
                result.stop_reason = "completed"

        if throttle_handler is not None:
            result.throttle_sleep_seconds = throttle_handler.total_sleep

        # Forward each action to the on_turn callback for trace logging.
        if on_turn:
            for i, (act, screenshot_path) in enumerate(action_pairs, start=1):
                try:
                    on_turn(i, {
                        "action": act,
                        "library": "browser-use",
                        "screenshot_path": screenshot_path,
                    })
                except Exception:
                    pass

    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        result.wall_seconds = time.monotonic() - started
        if throttle_handler is not None:
            result.throttle_sleep_seconds = throttle_handler.total_sleep
        result.token_usage_records = list(getattr(usage_handler, "records", []) or [])
        apply_token_records_to_result(result, result.token_usage_records)

    return result


def run_agent_session(**kwargs) -> SessionResult:
    """Sync wrapper around the async browser-use loop.

    run_session.py passes ``max_wall_seconds`` for schema consistency with the
    other 5 frameworks, but browser-use's internal step loop does not accept
    it; the wall-clock cap is enforced via ``asyncio.wait_for(time_limit_seconds)``
    when in time-pressure mode. We strip the kwarg here so the inner signature
    matches.
    """
    kwargs.pop("max_wall_seconds", None)
    return asyncio.run(_run_async(**kwargs))
