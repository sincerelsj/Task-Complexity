"""Layer-2 trace logger for agent sessions.

Writes the agent's internal reasoning / actions / screenshots to a mounted
host volume so post-hoc analysis of agent behavior is possible. Completely
orthogonal to the Flask app's ``/log`` and ``/submit`` endpoints, which
already capture the external behavioral signal (clicks, timings, final choice)
in the same schema as human participants.

Layout (under /workspace/traces, mounted to host
Task_Complexity/logs/agent_traces/):

    {framework_slug}/{model}/{subject}_{cond}_{ts}/
        messages.jsonl    — one line per API turn: assistant content + actions
        actions.jsonl     — one line per tool_use: flat action log
        screenshots/
            turn_001.png
            turn_002.png
            ...
        summary.json      — session-level metadata + token totals
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any, Optional

from .prompts import PROMPT_VERSION
from .token_accounting import aggregate_token_usage


class TraceLogger:
    def __init__(
        self,
        *,
        root: Path,
        framework_slug: str,
        agent_model: str,
        subject_id: str,
        condition_id: str,
        started_at_ms: int,
    ):
        self.session_dir = (
            root
            / framework_slug
            / agent_model
            / f"{subject_id}_{condition_id}_{started_at_ms}"
        )
        self.screenshots_dir = self.session_dir / "screenshots"
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.messages_path = self.session_dir / "messages.jsonl"
        self.actions_path = self.session_dir / "actions.jsonl"
        self.token_usage_path = self.session_dir / "token_usage.jsonl"
        self.summary_path = self.session_dir / "summary.json"
        self.started_at_ms = started_at_ms
        self.framework_slug = framework_slug
        self.agent_model = agent_model
        self.subject_id = subject_id
        self.condition_id = condition_id
        self.action_count = 0

    # --- Per-turn hook (called from loop.on_turn) ---------------------------
    def record_turn(self, turn: int, record: dict[str, Any]) -> None:
        """Append one turn's worth of assistant output and executed actions."""
        # Pull screenshots out of tool_result-adjacent context: here we log the
        # screenshot that was included in tool_use replies *by saving the most
        # recent screenshot file on disk*. The tools module already saves every
        # screenshot to /tmp/screenshots; we copy the latest one in if present.
        screenshot_path: Optional[str] = None
        if "screenshot_path" in record:
            # Some DOM-only runners (LCPW) explicitly decide which tool turns
            # get screenshots. When they pass None, do not fall back to the
            # latest global screenshot and accidentally attach it to read-only
            # tool calls.
            explicit_shot = record.get("screenshot_path")
            latest_shot = Path(explicit_shot) if explicit_shot else None
        else:
            latest_shot = _latest_screenshot()
        if latest_shot is not None and latest_shot.exists():
            dest = self.screenshots_dir / f"turn_{turn:03d}.png"
            try:
                dest.write_bytes(latest_shot.read_bytes())
                screenshot_path = str(dest.relative_to(self.session_dir))
            except Exception:
                pass

        actions = record.get("actions") or []
        if not actions and "action" in record:
            actions = [_normalize_action(record.get("action"), record.get("library"))]

        msg_entry = {
            "turn": turn,
            "ts_ms": int(time.time() * 1000),
            "prompt_version": PROMPT_VERSION,
            "stop_reason": record.get("stop_reason"),
            "library": record.get("library"),
            "plan": _json_safe(record.get("plan")),
            "observation": _json_safe(record.get("observation")),
            "step": _json_safe(record.get("step")),
            # The exact system prompt sent to the model for THIS turn's API call.
            # For static (no-deadline) runs this is the same string every turn;
            # for time-pressure runs it changes (remaining_seconds ticks down).
            # Logging it per-turn lets us reconstruct exactly what the model saw.
            "system_prompt": record.get("system_prompt"),
            # The user-side input the model saw on this turn — for the first
            # turn it's the task; for subsequent turns it's the tool_result
            # (text + redacted screenshot). Lets us audit the full conversation.
            "user_input": _redact_images(record.get("user_input") or []),
            "assistant": _redact_images(record.get("assistant") or []),
            "actions": _redact_images(_json_safe(actions)),
            "screenshot": screenshot_path,
        }
        with open(self.messages_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg_entry, ensure_ascii=False) + "\n")

        for a in actions:
            safe_action = _json_safe(a)
            flat = {
                "turn": turn,
                "ts_ms": msg_entry["ts_ms"],
                "tool_use_id": safe_action.get("tool_use_id"),
                "action": safe_action.get("action"),
                "input": safe_action.get("input"),
                "output": safe_action.get("output"),
                "observation": safe_action.get("observation"),
                "plan": safe_action.get("plan"),
                "library": safe_action.get("library") or record.get("library"),
                "computer_tool_type": safe_action.get("computer_tool_type"),
                "computer_use_beta": safe_action.get("computer_use_beta"),
                "had_error": safe_action.get("had_error"),
                "error": safe_action.get("error"),
            }
            with open(self.actions_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(flat, ensure_ascii=False) + "\n")
            self.action_count += 1

    # --- Per-LLM-call hook --------------------------------------------------
    def record_token_usage(self, record: dict[str, Any]) -> None:
        """Append one normalized provider/LangChain token usage record."""
        safe_record = _json_safe(record)
        entry = {
            "subject_id": self.subject_id,
            "condition_id": self.condition_id,
            "framework_slug": self.framework_slug,
            "agent_model": self.agent_model,
            "prompt_version": PROMPT_VERSION,
            **safe_record,
        }
        entry.setdefault("token_usage_status", entry.get("usage_status"))
        with open(self.token_usage_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # --- End-of-session summary --------------------------------------------
    def write_summary(
        self,
        *,
        iterations: int,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_creation_tokens: int,
        stop_reason: Optional[str],
        wall_seconds: float,
        final_text: str,
        error: Optional[str],
        task: str,
        url: str,
        throttle_sleep_seconds: float = 0.0,
        token_usage_records: Optional[list[dict[str, Any]]] = None,
        extra_summary_fields: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        token_usage_records = token_usage_records or []
        token_totals = aggregate_token_usage(
            token_usage_records,
            fallback_input_tokens=input_tokens,
            fallback_output_tokens=output_tokens,
            fallback_cache_read_tokens=cache_read_tokens,
            fallback_cache_creation_tokens=cache_creation_tokens,
        )
        if token_usage_records:
            for record in token_usage_records:
                self.record_token_usage(record)
        else:
            self.record_token_usage(_token_usage_fallback_record(token_totals))
        summary = {
            "subject_id": self.subject_id,
            "condition_id": self.condition_id,
            "framework_slug": self.framework_slug,
            "agent_model": self.agent_model,
            "prompt_version": PROMPT_VERSION,
            "started_at_ms": self.started_at_ms,
            "completed_at_ms": int(time.time() * 1000),
            "wall_seconds": round(wall_seconds, 3),
            "throttle_sleep_seconds": round(throttle_sleep_seconds, 3),
            "decision_seconds": round(wall_seconds - throttle_sleep_seconds, 3),
            "iterations": iterations,
            "action_count": self.action_count,
            "stop_reason": stop_reason,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_creation_tokens": cache_creation_tokens,
            **token_totals,
            "final_text": final_text,
            "error": error,
            "task_prompt": task,
            "url": url,
        }
        if extra_summary_fields:
            summary.update(extra_summary_fields)
        with open(self.summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        return summary


def _token_usage_fallback_record(token_totals: dict[str, Any]) -> dict[str, Any]:
    status = str(token_totals.get("token_usage_status") or "unavailable")
    mapping_status = "summary_only" if status == "legacy_summary_only" else "unavailable"
    return {
        "kind": "token_usage_unavailable",
        "token_accounting_version": token_totals.get("token_accounting_version"),
        "llm_call_index": None,
        "turn": None,
        "provider": "unknown",
        "model": None,
        "usage_source": "trace_logger_summary",
        "usage_status": status,
        "token_usage_status": status,
        "input_tokens_total": token_totals.get("input_tokens_total", 0),
        "input_tokens_uncached": token_totals.get("input_tokens_uncached", 0),
        "legacy_input_tokens": token_totals.get("legacy_input_tokens", 0),
        "cache_read_tokens": token_totals.get("cache_read_tokens_total", 0),
        "cache_creation_tokens": token_totals.get("cache_creation_tokens_total", 0),
        "output_tokens_total": token_totals.get("output_tokens_total", 0),
        "visible_output_tokens": token_totals.get("visible_output_tokens", 0),
        "reasoning_tokens": token_totals.get("reasoning_tokens", 0),
        "total_tokens": token_totals.get("total_tokens", 0),
        "mapping_status": mapping_status,
        "mapped_action_turn": None,
        "raw_usage_redacted": None,
        "note": (
            "No per-call provider token usage records were captured; "
            "use summary token_usage_status to decide token-analysis eligibility."
        ),
    }


def _redact_images(blocks: Any) -> Any:
    """Strip base64 image payloads from message content for compact JSONL logs.

    Images are persisted separately to screenshots/*.png; keeping them inline
    would bloat messages.jsonl by ~500KB per turn.
    """
    if isinstance(blocks, dict):
        if blocks.get("type") in ("image", "input_image", "computer_screenshot"):
            item = {k: _redact_images(v) for k, v in blocks.items()}
            if "image_url" in item:
                item["image_url"] = "<redacted>"
            if "source" in item:
                item["source"] = _redact_images(item["source"])
            return item

        cleaned = {}
        for key, value in blocks.items():
            if key == "image_url" and isinstance(value, str) and value.startswith("data:image"):
                cleaned[key] = "<redacted>"
            elif key == "data" and _looks_like_image_source(blocks):
                cleaned[key] = "<redacted>"
            else:
                cleaned[key] = _redact_images(value)
        return cleaned

    if isinstance(blocks, str) and blocks.startswith("data:image"):
        return "<redacted>"

    if not isinstance(blocks, list):
        return blocks

    cleaned: list[Any] = []
    for b in blocks:
        if not isinstance(b, dict):
            cleaned.append(b)
        else:
            cleaned.append(_redact_images(b))
    return cleaned


def _looks_like_image_source(block: dict[str, Any]) -> bool:
    source_type = str(block.get("type") or "").lower()
    media_type = str(block.get("media_type") or block.get("mime_type") or "").lower()
    return source_type in {"base64", "image", "input_image"} or media_type.startswith("image/")


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


def _normalize_action(action: Any, library: Optional[str] = None) -> dict[str, Any]:
    safe = _json_safe(action)
    if isinstance(safe, dict):
        action_name = (
            safe.get("action")
            or safe.get("tool")
            or safe.get("name")
        )
        action_input = safe.get("input") or safe.get("tool_input")
        if action_name is None:
            metadata_keys = {
                "interacted_element",
                "result",
                "error",
                "had_error",
                "library",
                "plan",
                "log",
                "output",
                "observation",
            }
            action_keys = [k for k in safe.keys() if k not in metadata_keys]
            if action_keys:
                action_name = action_keys[0]
                action_input = {
                    action_name: safe.get(action_name),
                }
                if "interacted_element" in safe:
                    action_input["interacted_element"] = safe.get(
                        "interacted_element")
        if action_name is None and len(safe) == 1:
            action_name = next(iter(safe.keys()))
            action_input = safe[action_name]
        return {
            "tool_use_id": safe.get("tool_use_id") or safe.get("id"),
            "action": action_name or "action",
            "input": action_input if action_input is not None else safe,
            "output": safe.get("output") or safe.get("observation"),
            "plan": safe.get("plan") or safe.get("log"),
            "library": library or safe.get("library"),
            "had_error": safe.get("had_error"),
            "error": safe.get("error"),
        }
    return {
        "tool_use_id": None,
        "action": "action",
        "input": safe,
        "output": None,
        "plan": None,
        "library": library,
        "had_error": None,
        "error": None,
    }


def _latest_screenshot() -> Optional[Path]:
    shots = Path("/tmp/screenshots")
    if not shots.exists():
        return None
    files = sorted(shots.glob("screen_*.png"))
    return files[-1] if files else None
