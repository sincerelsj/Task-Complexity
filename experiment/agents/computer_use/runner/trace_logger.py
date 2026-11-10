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
        self.summary_path = self.session_dir / "summary.json"
        self.started_at_ms = started_at_ms
        self.framework_slug = framework_slug
        self.agent_model = agent_model
        self.subject_id = subject_id
        self.condition_id = condition_id

    # --- Per-turn hook (called from loop.on_turn) ---------------------------
    def record_turn(self, turn: int, record: dict[str, Any]) -> None:
        """Append one turn's worth of assistant output and executed actions."""
        # Pull screenshots out of tool_result-adjacent context: here we log the
        # screenshot that was included in tool_use replies *by saving the most
        # recent screenshot file on disk*. The tools module already saves every
        # screenshot to /tmp/screenshots; we copy the latest one in if present.
        screenshot_path: Optional[str] = None
        latest_shot = _latest_screenshot()
        if latest_shot is not None:
            dest = self.screenshots_dir / f"turn_{turn:03d}.png"
            try:
                dest.write_bytes(latest_shot.read_bytes())
                screenshot_path = str(dest.relative_to(self.session_dir))
            except Exception:
                pass

        msg_entry = {
            "turn": turn,
            "ts_ms": int(time.time() * 1000),
            "stop_reason": record.get("stop_reason"),
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
            "actions": record.get("actions") or [],
            "screenshot": screenshot_path,
        }
        with open(self.messages_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg_entry, ensure_ascii=False) + "\n")

        for a in record.get("actions") or []:
            flat = {
                "turn": turn,
                "ts_ms": msg_entry["ts_ms"],
                "tool_use_id": a.get("tool_use_id"),
                "action": a.get("action"),
                "input": a.get("input"),
                "had_error": a.get("had_error"),
                "error": a.get("error"),
            }
            with open(self.actions_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(flat, ensure_ascii=False) + "\n")

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
    ) -> None:
        summary = {
            "subject_id": self.subject_id,
            "condition_id": self.condition_id,
            "framework_slug": self.framework_slug,
            "agent_model": self.agent_model,
            "started_at_ms": self.started_at_ms,
            "completed_at_ms": int(time.time() * 1000),
            "wall_seconds": round(wall_seconds, 3),
            "iterations": iterations,
            "stop_reason": stop_reason,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_creation_tokens": cache_creation_tokens,
            "final_text": final_text,
            "error": error,
            "task_prompt": task,
            "url": url,
        }
        with open(self.summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)


def _redact_images(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip base64 image payloads from message content for compact JSONL logs.

    Images are persisted separately to screenshots/*.png; keeping them inline
    would bloat messages.jsonl by ~500KB per turn.
    """
    cleaned: list[dict[str, Any]] = []
    for b in blocks:
        if b.get("type") == "image":
            cleaned.append({"type": "image", "source": {"type": "<redacted>"}})
        else:
            cleaned.append(b)
    return cleaned


def _latest_screenshot() -> Optional[Path]:
    shots = Path("/tmp/screenshots")
    if not shots.exists():
        return None
    files = sorted(shots.glob("screen_*.png"))
    return files[-1] if files else None
