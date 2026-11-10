from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

FRAMEWORK_LABELS = {
    "anthropic_computer_use": "ANTHCU",
    "openai_computer_use": "OAICU",
    "gemini_computer_use": "GEMCU",
    "claude_in_chrome": "CLDCR",
    "browser_use": "BROUSE",
    "langchain_playwright": "LCPW",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def load_conditions(root: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    path = root / "data" / "conditions.csv"
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            n_alt = int(row["n_alternatives"])
            n_dim = int(row["n_dimensions"])
            out[row["condition_id"]] = n_alt * n_dim
    return out


def load_events(root: Path, substring: str | None) -> dict[str, list[dict[str, str]]]:
    events_by_subject: dict[str, list[dict[str, str]]] = defaultdict(list)
    for path in (root / "logs").glob("events_*.csv"):
        if substring and substring not in path.name:
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            continue
        events_by_subject[rows[0]["subject_id"]].extend(rows)
    return events_by_subject


def canonical_action(action: dict[str, Any]) -> str:
    payload = {
        "action": action.get("action"),
        "input": action.get("input"),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


def operator_class(action: dict[str, Any]) -> str:
    """Coarse GOMS/KLM-inspired class for cross-framework action audits."""
    name = str(action.get("action", "")).lower()
    if not name:
        return "unknown"
    if "done" in name or name in {"finish", "final"}:
        return "done"
    if "click" in name or "tap" in name:
        return "motor_click"
    if "type" in name or "key" in name or "press" in name:
        return "motor_type"
    if "navigate" in name or "go_to_url" in name or "open_url" in name:
        return "navigation"
    if (
        "extract_text" in name
        or "get_elements" in name
        or "current_webpage" in name
        or "screenshot" in name
        or "observe" in name
    ):
        return "observe"
    if "scroll" in name or "mouse_move" in name or "move" in name:
        return "motor_move"
    return "other"


def completion_status(event_counts: Counter[str], info_count: int, expected_cells: int) -> str:
    if event_counts["final_choice"] and event_counts["session_end"]:
        return "submitted"
    if event_counts["choice_select"]:
        return "selected_not_submitted"
    if expected_cells and info_count >= expected_cells:
        return "full_search_no_submit"
    return "no_decision"


def click_miss_lower_bound(
    click_count: int,
    info_count: int,
    status: str,
) -> int:
    expected_non_info = 0
    if status == "submitted":
        expected_non_info = 2
    elif status == "selected_not_submitted":
        expected_non_info = 1
    return max(0, click_count - info_count - expected_non_info)


def audit(root: Path, substring: str | None) -> list[dict[str, Any]]:
    expected_cells = load_conditions(root)
    events_by_subject = load_events(root, substring)
    summaries = sorted((root / "logs" / "agent_traces").glob("**/summary.json"))
    if substring:
        summaries = [p for p in summaries if substring in str(p)]

    rows: list[dict[str, Any]] = []
    for summary_path in summaries:
        summary = read_json(summary_path)
        subject = summary.get("subject_id", "")
        condition = summary.get("condition_id", "")
        events = events_by_subject.get(subject, [])
        event_counts = Counter(row.get("event_type", "") for row in events)
        info_count = event_counts["info_view"]
        status = completion_status(
            event_counts,
            info_count,
            expected_cells.get(condition, 0),
        )

        actions = read_jsonl(summary_path.parent / "actions.jsonl")
        click_actions = [
            action for action in actions
            if "click" in str(action.get("action", "")).lower()
        ]
        repeats = Counter(canonical_action(action) for action in click_actions)
        action_error_count = sum(
            1 for action in actions
            if bool(action.get("had_error")) or bool(action.get("error"))
        )
        operator_counts = Counter(operator_class(action) for action in actions)
        method_sequence = " ".join(operator_class(action) for action in actions[:24])
        if len(actions) > 24:
            method_sequence += " ..."

        rows.append({
            "framework": FRAMEWORK_LABELS.get(
                summary.get("framework_slug", ""),
                summary.get("framework_slug", ""),
            ),
            "model": summary.get("agent_model", ""),
            "condition": condition,
            "status": status,
            "stop": summary.get("stop_reason", ""),
            "has_error": bool(summary.get("error")),
            "iterations": summary.get("iterations", 0),
            "actions": summary.get("action_count", len(actions)),
            "clicks": len(click_actions),
            "info_views": info_count,
            "choice_select": event_counts["choice_select"],
            "final_choice": event_counts["final_choice"],
            "session_end": event_counts["session_end"],
            "observe_ops": operator_counts["observe"],
            "motor_click_ops": operator_counts["motor_click"],
            "motor_type_ops": operator_counts["motor_type"],
            "motor_move_ops": operator_counts["motor_move"],
            "navigation_ops": operator_counts["navigation"],
            "done_ops": operator_counts["done"],
            "other_ops": operator_counts["other"] + operator_counts["unknown"],
            "method_prefix": method_sequence,
            "action_errors": action_error_count,
            "miss_lb": click_miss_lower_bound(len(click_actions), info_count, status),
            "max_repeat": max(repeats.values()) if repeats else 0,
            "summary_error": (summary.get("error") or "")[:80],
            "trace_dir": str(summary_path.parent.relative_to(root)),
        })
    return rows


def print_markdown(rows: list[dict[str, Any]]) -> None:
    headers = [
        "framework", "model", "condition", "status", "stop", "has_error",
        "iterations", "actions", "clicks", "info_views", "choice_select",
        "final_choice", "session_end", "observe_ops", "motor_click_ops",
        "navigation_ops", "done_ops", "action_errors", "miss_lb", "max_repeat",
    ]
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        print("| " + " | ".join(str(row[h]) for h in headers) + " |")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Join Layer-2 actions with Layer-1 events for agent run audits."
    )
    parser.add_argument(
        "--substring",
        help="Only include trace/event paths containing this string, e.g. batch_20260602_094059.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Project root. Defaults to the parent of this analysis directory.",
    )
    args = parser.parse_args()

    rows = audit(args.root.resolve(), args.substring)
    rows.sort(key=lambda r: (r["condition"], r["framework"], r["model"]))
    print_markdown(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
