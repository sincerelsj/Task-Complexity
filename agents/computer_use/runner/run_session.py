"""CLI entry point for one Anthropic Computer Use session.

Invoked by image/entrypoint.sh after the display stack and Chromium are up.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/computeruse")

from agents.registry import FRAMEWORK_CODES  # noqa: E402
from runner.loop import DEFAULT_MODEL, DEFAULT_THINKING_MODE, run_agent_session  # noqa: E402
from shared.pricing import estimate_usage
from shared.token_accounting import aggregate_token_usage  # noqa: E402
from shared.trace_logger import TraceLogger  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run one Computer Use session.")
    p.add_argument("--url", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--subject-id", required=True)
    p.add_argument("--condition-id", required=True)
    p.add_argument("--framework-code", default="ANTHCU")
    p.add_argument("--agent-model", default=DEFAULT_MODEL)
    p.add_argument("--max-iterations", type=int, default=80)
    p.add_argument("--max-wall-seconds", type=float, default=30 * 60)
    p.add_argument(
        "--time-limit-seconds",
        type=float,
        default=None,
        help=(
            "Optional soft deadline (seconds) shown to the model. When set "
            "(>0), enables the TIME PRESSURE variant."
        ),
    )
    p.add_argument(
        "--turn-sleep-seconds",
        type=float,
        default=0.0,
        help=(
            "Fixed wall-clock sleep inserted before every API call except "
            "the first. Used for rate-limit throttling. Default 0 = off. "
            "Excluded from time-pressure deadline elapsed."
        ),
    )
    p.add_argument(
        "--thinking-mode",
        default=os.environ.get("ANTHCU_THINKING_MODE", DEFAULT_THINKING_MODE),
        choices=["off", "auto", "adaptive", "manual"],
        help=(
            "Anthropic thinking mode for ANTHCU. Default auto uses adaptive "
            "thinking on Opus/Sonnet 4.6+ and manual thinking on Haiku 4.5. "
            "Use off to disable thinking."
        ),
    )
    p.add_argument(
        "--thinking-display",
        default=os.environ.get("ANTHCU_THINKING_DISPLAY", "summarized"),
        choices=["summarized", "omitted"],
        help="How returned thinking blocks are displayed when thinking is on.",
    )
    p.add_argument(
        "--thinking-effort",
        default=os.environ.get("ANTHCU_THINKING_EFFORT") or None,
        choices=["low", "medium", "high", "xhigh", "max"],
        help="Optional adaptive-thinking effort guidance.",
    )
    p.add_argument(
        "--thinking-budget-tokens",
        type=int,
        default=int(os.environ.get("ANTHCU_THINKING_BUDGET_TOKENS", "4096")),
        help="Manual thinking budget for Haiku/older-model runs.",
    )
    p.add_argument("--traces-root", default="/workspace/traces")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    framework_code = args.framework_code.upper()
    if framework_code not in FRAMEWORK_CODES:
        print(f"[run_session] ERROR: unknown framework_code {framework_code!r}",
              file=sys.stderr)
        return 2
    framework_slug = FRAMEWORK_CODES[framework_code]

    time_limit_seconds = args.time_limit_seconds
    if time_limit_seconds is not None and time_limit_seconds > 0:
        framework_slug = f"{framework_slug}_time_pressure"
    else:
        time_limit_seconds = None

    # thinking-OFF arm gets its own slug so Layer-1 (agent_framework) and Layer-2
    # (trace folder) both separate it from the thinking-ON arm (docs/16 §1.1).
    if (args.thinking_mode or "").strip().lower() == "off":
        framework_slug = f"{framework_slug}_thinkoff"

    started_at_ms = int(time.time() * 1000)
    traces_root = Path(args.traces_root)
    traces_root.mkdir(parents=True, exist_ok=True)

    tracer = TraceLogger(
        root=traces_root,
        framework_slug=framework_slug,
        agent_model=args.agent_model,
        subject_id=args.subject_id,
        condition_id=args.condition_id,
        started_at_ms=started_at_ms,
    )

    print(f"[run_session] starting subject={args.subject_id} "
          f"cond={args.condition_id} model={args.agent_model}")
    if time_limit_seconds is not None:
        print(f"[run_session] TIME PRESSURE mode: {int(time_limit_seconds)}s deadline")
    if args.turn_sleep_seconds and args.turn_sleep_seconds > 0:
        print(f"[run_session] throttle: {args.turn_sleep_seconds}s between turns "
              f"(rate-limit avoidance; excluded from time-pressure deadline)")
    if args.thinking_mode != "off":
        print(f"[run_session] thinking: mode={args.thinking_mode} "
              f"display={args.thinking_display} effort={args.thinking_effort} "
              f"budget={args.thinking_budget_tokens}")
    print(f"[run_session] trace dir: {tracer.session_dir}")

    result = run_agent_session(
        task=args.task,
        model=args.agent_model,
        max_iterations=args.max_iterations,
        max_wall_seconds=args.max_wall_seconds,
        time_limit_seconds=time_limit_seconds,
        turn_sleep_seconds=args.turn_sleep_seconds,
        thinking_mode=args.thinking_mode,
        thinking_display=args.thinking_display,
        thinking_effort=args.thinking_effort,
        thinking_budget_tokens=args.thinking_budget_tokens,
        on_turn=tracer.record_turn,
    )
    token_usage_records = list(getattr(result, "token_usage_records", []) or [])
    token_totals = aggregate_token_usage(
        token_usage_records,
        fallback_input_tokens=result.input_tokens,
        fallback_output_tokens=result.output_tokens,
        fallback_cache_read_tokens=result.cache_read_tokens,
        fallback_cache_creation_tokens=result.cache_creation_tokens,
    )

    tracer.write_summary(
        iterations=result.iterations,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read_tokens=result.cache_read_tokens,
        cache_creation_tokens=result.cache_creation_tokens,
        stop_reason=result.stop_reason,
        wall_seconds=result.wall_seconds,
        final_text=result.final_text,
        error=result.error,
        task=args.task,
        url=args.url,
        throttle_sleep_seconds=result.throttle_sleep_seconds,
        token_usage_records=token_usage_records,
        extra_summary_fields={
            "anthcu_thinking_mode": result.thinking_mode,
            "anthcu_thinking_display": result.thinking_display,
            "anthcu_thinking_effort": result.thinking_effort,
            "anthcu_thinking_budget_tokens": result.thinking_budget_tokens,
        },
    )

    cost_estimate = estimate_usage(token_totals, args.agent_model)

    print(json.dumps({
        "ok": result.error is None,
        "iterations": result.iterations,
        "stop_reason": result.stop_reason,
        "wall_seconds": round(result.wall_seconds, 2),
        "throttle_sleep_seconds": round(result.throttle_sleep_seconds, 2),
        "input_tokens": result.input_tokens,
        "token_usage_status": token_totals["token_usage_status"],
        "token_usage_calls": token_totals["token_usage_calls"],
        "input_tokens_total": token_totals["input_tokens_total"],
        "input_tokens_uncached": token_totals["input_tokens_uncached"],
        "output_tokens": result.output_tokens,
        "output_tokens_total": token_totals["output_tokens_total"],
        "visible_output_tokens": token_totals["visible_output_tokens"],
        "reasoning_tokens": token_totals["reasoning_tokens"],
        "cache_read_tokens": result.cache_read_tokens,
        "cache_creation_tokens": result.cache_creation_tokens,
        "total_tokens": token_totals["total_tokens"],
        "anthcu_thinking_mode": result.thinking_mode,
        "anthcu_thinking_display": result.thinking_display,
        "anthcu_thinking_effort": result.thinking_effort,
        "anthcu_thinking_budget_tokens": result.thinking_budget_tokens,
        "estimated_cost_usd": round(cost_estimate["usd"], 6) if cost_estimate["usd"] is not None else None,
        "cost_estimate_status": cost_estimate["status"],
        "cost_input_rate_per_million": cost_estimate.get("rates_per_million", {}).get("input"),
        "cost_output_rate_per_million": cost_estimate.get("rates_per_million", {}).get("output"),
        "cost_cache_write_rate_per_million": cost_estimate.get("rates_per_million", {}).get("cache_write"),
        "cost_cache_read_rate_per_million": cost_estimate.get("rates_per_million", {}).get("cache_read"),
        "error": result.error,
        "trace_dir": str(tracer.session_dir),
    }, ensure_ascii=False, indent=2))

    return 0 if result.error is None else 1


if __name__ == "__main__":
    sys.exit(main())
