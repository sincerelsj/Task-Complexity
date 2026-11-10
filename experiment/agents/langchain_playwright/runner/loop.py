"""LangChain PlaywrightBrowserToolkit agent loop.

DOM-level browsing agent: the model never sees screenshots; it sees
extracted text and structured elements (CSS selectors, hyperlinks) and acts
through tools like:

    NavigateTool, NavigateBackTool,
    ClickTool (selector-based),
    ExtractTextTool, ExtractHyperlinksTool, GetElementsTool,
    CurrentWebPageTool

We pre-navigate to the experiment URL, then run an
``AgentExecutor(create_tool_calling_agent)`` with an Anthropic (or other)
LangChain LLM. The Layer-1 behavioral data still lands correctly because
ClickTool dispatches real DOM events.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from shared.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT as _BASE_PROMPT,
    TOOL_SECTION_HEADER,
    TIME_PRESSURE_SYSTEM_PROMPT_TEMPLATE,
)
from shared.token_accounting import apply_token_records_to_result, build_langchain_usage_handler


DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_MAX_ITERATIONS = 80
DEFAULT_MAX_WALL_SECONDS = 30 * 60
LCPW_INTERMEDIATE_STEP_WINDOW = int(os.environ.get("LCPW_INTERMEDIATE_STEP_WINDOW", "12"))
LCPW_EXTRACT_TEXT_OBSERVATION_WINDOW = int(os.environ.get(
    "LCPW_EXTRACT_TEXT_OBSERVATION_WINDOW",
    os.environ.get("LCPW_DOM_OBSERVATION_WINDOW", "1"),
))
LCPW_GET_ELEMENTS_OBSERVATION_WINDOW = int(os.environ.get(
    "LCPW_GET_ELEMENTS_OBSERVATION_WINDOW",
    "5",
))
LCPW_DOM_OBSERVATION_WINDOWS = {
    "extract_text": LCPW_EXTRACT_TEXT_OBSERVATION_WINDOW,
    "get_elements": LCPW_GET_ELEMENTS_OBSERVATION_WINDOW,
}
LCPW_DOM_OBSERVATION_TOOL_NAMES = set(LCPW_DOM_OBSERVATION_WINDOWS)
LCPW_ALLOWED_TOOL_NAMES = {"current_webpage", "extract_text", "get_elements"}
# Behavioral-loop guard (parity with ANTHCU/CLDCR/OAICU/GEMCU, 2026-06-11).
# LCPW counts consecutive identical *click_element* selectors; read-only tools
# (extract_text / get_elements / current_webpage) neither count nor reset, so
# the click-A / extract / click-A / extract alternation that stalled
# LCPW haiku C6 for 36 minutes is caught.
REPEATED_ACTION_GUARD_LIMIT = int(os.environ.get("LCPW_REPEATED_ACTION_GUARD_LIMIT", "8"))


class RepeatedActionGuardError(RuntimeError):
    """Raised from the click tool when the same selector is clicked
    REPEATED_ACTION_GUARD_LIMIT times in a row (ignoring read-only tools)."""


_LANGCHAIN_TOOL_GUIDANCE = """\
- `extract_text` to read what's currently visible on the page
- `get_elements` with a CSS selector to find clickable cells / buttons
- `click_element` (selector-based) to click cells (which expand to reveal
  information). Clicking an apartment choice button submits the task
  immediately; there is no separate submit button.
- `current_webpage` to verify you're still on the experiment page
"""

SYSTEM_PROMPT = _BASE_PROMPT + TOOL_SECTION_HEADER + _LANGCHAIN_TOOL_GUIDANCE


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

    Same pattern as agents/browser_use; duplicated to keep each framework
    self-contained (no cross-framework imports).
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
    if sleep_seconds <= 0:
        return None
    try:
        from langchain_core.callbacks import AsyncCallbackHandler  # type: ignore
    except Exception:
        from langchain.callbacks.base import AsyncCallbackHandler  # type: ignore

    class _Handler(AsyncCallbackHandler, _ThrottleCallback):  # type: ignore[misc]
        def __init__(self, sleep_seconds: float, time_limit_seconds: Optional[float] = None):
            AsyncCallbackHandler.__init__(self)
            _ThrottleCallback.__init__(self, sleep_seconds, time_limit_seconds)

        async def on_chat_model_start(self, *args, **kwargs):
            await _ThrottleCallback.on_chat_model_start(self, *args, **kwargs)

        async def on_llm_start(self, *args, **kwargs):
            await _ThrottleCallback.on_llm_start(self, *args, **kwargs)

    return _Handler(sleep_seconds, time_limit_seconds)


def _build_async_screenshot_handler(page: Any):
    """Capture trace-only screenshots after LCPW click tool executions.

    LCPW stays DOM-only from the model's point of view. These PNGs are saved
    only for Layer-2 audit/debug traces and are never inserted into the prompt.
    Read-only tools such as extract_text/get_elements/current_webpage do not
    get screenshots.
    """
    try:
        from langchain_core.callbacks import AsyncCallbackHandler  # type: ignore
    except Exception:
        from langchain.callbacks.base import AsyncCallbackHandler  # type: ignore

    class _Handler(AsyncCallbackHandler):  # type: ignore[misc]
        def __init__(self):
            super().__init__()
            self.count = 0
            self.paths: list[Optional[str]] = []
            self._tool_names: dict[str, Optional[str]] = {}

        async def _capture(self) -> None:
            self.count += 1
            shots_dir = Path("/tmp/screenshots")
            try:
                shots_dir.mkdir(parents=True, exist_ok=True)
                path = shots_dir / f"screen_lcpw_{self.count:03d}.png"
                await page.screenshot(path=str(path), full_page=False)
                self.paths.append(str(path))
            except Exception:
                self.paths.append(None)

        async def _maybe_capture(self, **kwargs) -> None:
            run_id = kwargs.get("run_id")
            tool_name = self._tool_names.pop(str(run_id), None) if run_id is not None else None
            if tool_name == "click_element":
                await self._capture()
            else:
                self.paths.append(None)

        async def on_tool_start(self, serialized, *args, **kwargs):
            run_id = kwargs.get("run_id")
            if run_id is None:
                return
            name = None
            if isinstance(serialized, dict):
                name = serialized.get("name")
            self._tool_names[str(run_id)] = name

        async def on_tool_end(self, *args, **kwargs):
            await self._maybe_capture(**kwargs)

        async def on_tool_error(self, *args, **kwargs):
            await self._maybe_capture(**kwargs)

    return _Handler()


def _build_llm(model: str, callbacks=None):
    """Return a LangChain LLM instance.

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
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(model_name=model, temperature=0.0, max_tokens=20000, callbacks=cb)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump())
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


def _step_tool_name(step: Any) -> Optional[str]:
    try:
        agent_action, _ = step
    except Exception:
        agent_action = step

    tool = getattr(agent_action, "tool", None)
    if tool is None and isinstance(agent_action, dict):
        tool = agent_action.get("tool") or agent_action.get("name")
    return str(tool) if tool is not None else None


def _trim_lcpw_intermediate_steps(intermediate_steps: list[Any]) -> list[Any]:
    """Keep DOM read observations tiny while preserving recent action context."""
    if not intermediate_steps:
        return intermediate_steps

    action_window = max(LCPW_INTERMEDIATE_STEP_WINDOW, 0)
    kept_reversed: list[Any] = []
    action_count = 0
    dom_counts: dict[str, int] = {}

    for step in reversed(intermediate_steps):
        tool_name = _step_tool_name(step)
        if tool_name in LCPW_DOM_OBSERVATION_TOOL_NAMES:
            dom_window = max(LCPW_DOM_OBSERVATION_WINDOWS.get(tool_name, 0), 0)
            seen = dom_counts.get(tool_name, 0)
            if seen < dom_window:
                kept_reversed.append(step)
                dom_counts[tool_name] = seen + 1
            continue

        if action_count < action_window:
            kept_reversed.append(step)
            action_count += 1

    return list(reversed(kept_reversed))


def _step_to_action_record(step: Any) -> dict[str, Any]:
    try:
        agent_action, observation = step
    except Exception:
        agent_action, observation = step, None

    tool = getattr(agent_action, "tool", None)
    tool_input = getattr(agent_action, "tool_input", None)
    tool_call_id = getattr(agent_action, "tool_call_id", None)
    log = getattr(agent_action, "log", None)

    if isinstance(agent_action, dict):
        tool = tool or agent_action.get("tool") or agent_action.get("name")
        tool_input = tool_input if tool_input is not None else agent_action.get("tool_input")
        tool_call_id = tool_call_id or agent_action.get("tool_call_id") or agent_action.get("id")
        log = log or agent_action.get("log")

    obs = _json_safe(observation)
    error_text = None
    if isinstance(obs, str) and obs.lower().startswith("unable to"):
        error_text = obs

    return {
        "tool_use_id": tool_call_id,
        "action": tool or type(agent_action).__name__,
        "input": _json_safe(tool_input),
        "output": obs,
        "observation": obs,
        "plan": log,
        "library": "langchain-playwright",
        "had_error": error_text is not None,
        "error": error_text,
    }


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
    # Imports are deferred so the file remains py_compile-able without the
    # heavyweight LangChain stack present.
    from langchain_community.agent_toolkits import PlayWrightBrowserToolkit
    from playwright.async_api import async_playwright
    from langchain.agents import AgentExecutor, create_tool_calling_agent
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.tools import StructuredTool

    # Headed browser on Xvfb (so VNC can show it). Do not use LangChain's
    # create_async_playwright_browser helper here: in this package version it
    # calls run_until_complete(), which fails inside our already-running async
    # loop. Launch Playwright directly instead.
    playwright = await async_playwright().start()
    async_browser = await playwright.chromium.launch(
        headless=False,
        executable_path="/usr/bin/chromium",
        args=[
            "--no-sandbox",
            "--disable-gpu-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--no-xshm",
            "--window-size=1920,1200",
        ],
    )

    # Pre-navigate so the agent doesn't have to (and so any "navigation
    # disabled" guardrail in the prompt doesn't trip on the very first step).
    page = await async_browser.new_page()
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    await asyncio.sleep(1.5)

    toolkit = PlayWrightBrowserToolkit.from_browser(async_browser=async_browser)
    tools = toolkit.get_tools()
    tools = [
        tool for tool in tools
        if getattr(tool, "name", "") in LCPW_ALLOWED_TOOL_NAMES
    ]

    repeat_state = {"selector": None, "count": 0}

    async def _click_element(selector: str) -> str:
        # Repeated identical click guard. Counts consecutive identical
        # selectors across click calls (read-only tools in between do not
        # reset the streak because they never call this function).
        if selector == repeat_state["selector"]:
            repeat_state["count"] += 1
        else:
            repeat_state["selector"] = selector
            repeat_state["count"] = 1
        if repeat_state["count"] >= REPEATED_ACTION_GUARD_LIMIT:
            raise RepeatedActionGuardError(
                f"repeated identical click {repeat_state['count']} times: "
                f"click_element {{'selector': '{selector}'}}"
            )
        try:
            count = await page.locator(selector).count()
            if count < 1:
                return f"Unable to click on element '{selector}'"
            locator = page.locator(selector).first
            try:
                await locator.click(timeout=3_000)
            except Exception:
                await page.eval_on_selector(
                    selector,
                    "el => { el.scrollIntoView({block: 'center', inline: 'center'}); el.click(); }",
                )
            completion_hint = ""
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=2_000)
            except Exception:
                pass
            try:
                body_text = await page.locator("body").inner_text(timeout=2_000)
            except Exception:
                body_text = ""
            body_lower = body_text.lower()
            if (
                "thank" in body_lower
                or "saved" in body_lower
                or "completed" in body_lower
                or "감사" in body_text
                or "저장" in body_text
            ):
                completion_hint = (
                    " Experiment submitted successfully; the task is complete. "
                    "Do not call more tools."
                )
            elif "data-choice" in selector:
                completion_hint = (
                    " Choice buttons submit immediately; stop acting unless the "
                    "page reports an error."
                )
            return f"Clicked element '{selector}'.{completion_hint}"
        except Exception as e:
            return f"Unable to click on element '{selector}': {type(e).__name__}: {e}"

    click_tool = StructuredTool.from_function(
        coroutine=_click_element,
        name="click_element",
        description=(
            "Click the first element matching a CSS selector on the current "
            "experiment page. Use selectors such as button.info-cell, "
            "or [data-choice='A']. Choice buttons submit the final choice "
            "immediately; there is no separate submit button."
        ),
    )
    tools = [tool for tool in tools if getattr(tool, "name", "") != "click_element"]
    tools.append(click_tool)

    throttle_handler = _build_async_throttle_handler(
        turn_sleep_seconds,
        time_limit_seconds if time_limit_seconds and time_limit_seconds > 0 else None,
    )
    usage_handler = build_langchain_usage_handler(model)
    callbacks = [h for h in (throttle_handler, usage_handler) if h is not None]
    callbacks = callbacks or None
    llm = _build_llm(model, callbacks=callbacks)
    screenshot_handler = _build_async_screenshot_handler(page)

    # Determine the system prompt: static for baseline, dynamically updated
    # for time-pressure. When time_limit_seconds is None, use the static
    # SYSTEM_PROMPT. When set, we'll format it once at start with the initial
    # remaining time, and LangChain's internal loop will use that for all turns.
    # (Unlike ANTHCU, AgentExecutor doesn't expose per-turn system prompt
    # refresh, so we apply the deadline via max_execution_time and a hard timeout.)
    if time_limit_seconds is None:
        system_prompt_text = SYSTEM_PROMPT
    else:
        system_prompt_text = (
            TIME_PRESSURE_SYSTEM_PROMPT_TEMPLATE.format(
                time_limit_seconds=int(time_limit_seconds),
                remaining_seconds=int(time_limit_seconds),
            )
            + TOOL_SECTION_HEADER
            + _LANGCHAIN_TOOL_GUIDANCE
        )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt_text),
        ("user", "Task: {input}\n\nThe experiment page is already loaded at {url}."),
        ("placeholder", "{agent_scratchpad}"),
    ])
    agent = create_tool_calling_agent(llm, tools, prompt)

    # When time_limit_seconds is set, use max_execution_time as a soft safety cap
    # on the AgentExecutor internal loop. This is best-effort — the model may still
    # make calls after the deadline (the hard timeout is applied at invoke time).
    executor_kwargs = {
        "agent": agent,
        "tools": tools,
        "verbose": True,
        "max_iterations": max_iterations,
        "return_intermediate_steps": True,
        # Prevent old DOM/text observations from being replayed into every
        # later model call. extract_text keeps only the latest observation;
        # get_elements keeps a small recent window for selector recovery.
        # Recent non-DOM action outcomes stay as short memory.
        # The full untrimmed intermediate_steps list is still returned at the
        # end for Layer-2 trace/action logging.
        "trim_intermediate_steps": _trim_lcpw_intermediate_steps,
        "callbacks": [screenshot_handler],
    }
    if time_limit_seconds is not None and time_limit_seconds > 0:
        # Extend by max throttle budget so throttle doesn't eat into the
        # agent's nominal decision-time deadline.
        throttle_budget = max(max_iterations - 1, 0) * max(turn_sleep_seconds, 0.0)
        executor_kwargs["max_execution_time"] = time_limit_seconds + throttle_budget
    executor = AgentExecutor(**executor_kwargs)

    # Log session initialization: system prompt + full task to messages.jsonl.
    # LangChain's AgentExecutor wraps its internal loop; we cannot capture per-turn
    # system prompts, so log the initial state once at session start.
    if trace_messages_path is not None:
        import json
        from pathlib import Path
        init_entry = {
            "turn": 0,
            "ts_ms": int(time.time() * 1000),
            "kind": "session_init",
            "prompt_version": PROMPT_VERSION,
            "system_prompt": system_prompt_text,  # Full system prompt sent to LLM
            "user_input": task,                   # Full task input
            "library": "langchain AgentExecutor",
            "note": "Library-internal loop; per-turn API messages not visible to outer code.",
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
        out = await executor.ainvoke(
            {"input": task, "url": url},
            config={"callbacks": [screenshot_handler]},
        )
        result.wall_seconds = time.monotonic() - started
        result.final_text = str(out.get("output") or "")

        steps = out.get("intermediate_steps") or []
        result.iterations = len(steps)

        if throttle_handler is not None:
            result.throttle_sleep_seconds = throttle_handler.total_sleep
        result.token_usage_records = list(getattr(usage_handler, "records", []) or [])
        apply_token_records_to_result(result, result.token_usage_records)

        decision_elapsed = result.wall_seconds - result.throttle_sleep_seconds

        # Determine stop_reason: check if we hit the decision-time deadline,
        # excluding rate-limit throttle sleeps from the model's 180s budget.
        if (time_limit_seconds is not None
                and decision_elapsed >= time_limit_seconds):
            result.stop_reason = "time_pressure_exceeded"
        elif "max iterations" in result.final_text.lower():
            result.stop_reason = "max_iterations"
            result.error = f"max iterations ({max_iterations}) reached without completion"
        elif result.stop_reason is None:
            result.stop_reason = "completed"

        # Forward intermediate steps to the on_turn callback for trace logging.
        if on_turn:
            for i, step in enumerate(steps, start=1):
                try:
                    action_record = _step_to_action_record(step)
                    on_turn(i, {
                        "library": "langchain-playwright",
                        "plan": action_record.get("plan"),
                        "observation": action_record.get("observation"),
                        "screenshot_path": (
                            screenshot_handler.paths[i - 1]
                            if i - 1 < len(screenshot_handler.paths) else None
                        ),
                        "assistant": (
                            [{"type": "text", "text": action_record.get("plan")}]
                            if action_record.get("plan") else []
                        ),
                        "actions": [action_record],
                    })
                except Exception:
                    pass

    except asyncio.TimeoutError as e:
        result.error = str(e) or f"time_pressure_exceeded: deadline {int(time_limit_seconds or 0)}s"
        result.stop_reason = "time_pressure_exceeded"
        result.wall_seconds = time.monotonic() - started
        if throttle_handler is not None:
            result.throttle_sleep_seconds = throttle_handler.total_sleep

    except RepeatedActionGuardError as e:
        result.error = str(e)
        result.stop_reason = "repeated_action_guard"
        result.wall_seconds = time.monotonic() - started
        if throttle_handler is not None:
            result.throttle_sleep_seconds = throttle_handler.total_sleep
        result.token_usage_records = list(getattr(usage_handler, "records", []) or [])
        apply_token_records_to_result(result, result.token_usage_records)

    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        result.wall_seconds = time.monotonic() - started
        if throttle_handler is not None:
            result.throttle_sleep_seconds = throttle_handler.total_sleep
        result.token_usage_records = list(getattr(usage_handler, "records", []) or [])
        apply_token_records_to_result(result, result.token_usage_records)

    finally:
        try:
            await async_browser.close()
        except Exception:
            pass
        try:
            await playwright.stop()
        except Exception:
            pass

    return result


def run_agent_session(**kwargs) -> SessionResult:
    """Sync wrapper around the async LangChain Playwright loop.

    run_session.py passes ``max_wall_seconds`` for schema consistency with the
    other 5 frameworks, but the inner async loop relies on the agent executor's
    own step budget plus ``time_limit_seconds`` for hard wall-clock enforcement.
    Strip the kwarg so the inner signature matches.
    """
    kwargs.pop("max_wall_seconds", None)
    return asyncio.run(_run_async(**kwargs))
