from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

LOOPS = {
    "ANTHCU": ROOT / "agents/computer_use/runner/loop.py",
    "OAICU": ROOT / "agents/openai_computer_use/runner/loop.py",
    "GEMCU": ROOT / "agents/gemini_computer_use/runner/loop.py",
    "CLDCR": ROOT / "agents/claude_in_chrome/runner/loop.py",
    "BROUSE": ROOT / "agents/browser_use/runner/loop.py",
    "LCPW": ROOT / "agents/langchain_playwright/runner/loop.py",
}

RUN_SESSIONS = {
    code: path.parent / "run_session.py"
    for code, path in LOOPS.items()
}

COMPOSE = {
    "ANTHCU": ROOT / "agents/computer_use/docker-compose.yml",
    "OAICU": ROOT / "agents/openai_computer_use/docker-compose.yml",
    "GEMCU": ROOT / "agents/gemini_computer_use/docker-compose.yml",
    "CLDCR": ROOT / "agents/claude_in_chrome/docker-compose.yml",
    "BROUSE": ROOT / "agents/browser_use/docker-compose.yml",
    "LCPW": ROOT / "agents/langchain_playwright/docker-compose.yml",
}

EXPECTED_DEFAULT_MODELS = {
    "ANTHCU": 'DEFAULT_MODEL = "claude-sonnet-4-6"',
    "OAICU": 'DEFAULT_MODEL = "gpt-5.4-mini"',
    "GEMCU": 'DEFAULT_MODEL = "gemini-3-flash-preview"',
    "CLDCR": 'DEFAULT_MODEL = "claude-haiku-4-5"',
    "BROUSE": 'DEFAULT_MODEL = "gpt-5.4-mini"',
    "LCPW": 'DEFAULT_MODEL = "claude-haiku-4-5"',
}

EXPECTED_DIRECT_MAX_TOKENS = {
    "ANTHCU": "DEFAULT_MAX_TOKENS = 20000",
    "OAICU": "DEFAULT_MAX_TOKENS = 20000",
    "GEMCU": "DEFAULT_MAX_TOKENS = 20000",
    "CLDCR": "DEFAULT_MAX_TOKENS = 20000",
}

EXPECTED_DIRECT_TEMPERATURE = {
    "OAICU": "DEFAULT_TEMPERATURE = 0.0",
    "GEMCU": "DEFAULT_TEMPERATURE = 0.0",
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(errors: list[str], label: str, condition: bool) -> None:
    if not condition:
        errors.append(label)


def assigned_constant(path: Path, name: str) -> object:
    tree = ast.parse(read(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name and node.value is not None:
                return ast.literal_eval(node.value)
    raise KeyError(name)


def compose_env_value(text: str, key: str) -> str | None:
    match = re.search(rf"^\s*{re.escape(key)}:\s*\"?([^\"\r\n]+)\"?", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def main() -> int:
    errors: list[str] = []

    token_accounting = read(ROOT / "agents/_shared/token_accounting.py")
    trace_logger = read(ROOT / "agents/_shared/trace_logger.py")
    require(errors, "shared token accounting versioned", "TOKEN_ACCOUNTING_VERSION" in token_accounting)
    require(errors, "shared token accounting normalizes Anthropic usage", "def usage_from_anthropic" in token_accounting)
    require(errors, "shared token accounting normalizes OpenAI usage", "def usage_from_openai" in token_accounting)
    require(errors, "shared token accounting normalizes Gemini usage", "def usage_from_gemini" in token_accounting)
    require(errors, "shared token accounting handles LangChain callbacks", "def build_langchain_usage_handler" in token_accounting)
    require(errors, "LangChain token usage handler has no-op start hooks", "async def on_chat_model_start" in token_accounting and "async def on_llm_start" in token_accounting)
    require(errors, "TraceLogger writes token_usage.jsonl", "token_usage.jsonl" in trace_logger and "record_token_usage" in trace_logger)
    require(
        errors,
        "TraceLogger writes token usage status",
        "**token_totals" in trace_logger and "token_usage_records" in trace_logger,
    )

    agents_md = read(ROOT / "CONTRIBUTING.md")
    require(errors, "AGENTS links experimental control matrix", "docs/10_experimental_control_matrix.md" in agents_md)
    require(errors, "AGENTS names shared prompt as source of truth", "agents/_shared/prompts.py" in agents_md)
    require(errors, "AGENTS includes verifier command", "python tools\\verify_experimental_controls.py" in agents_md)

    control_matrix = read(ROOT / "docs/10_experimental_control_matrix.md")
    require(errors, "control matrix documents one-action policy", "A screenshot authorizes exactly one UI action." in control_matrix)
    require(errors, "control matrix documents nested screenshot pruning", "nested `tool_result.content[]` image blocks" in control_matrix)
    require(errors, "control matrix documents LCPW DOM observation pruning", "LCPW trims old DOM/text intermediate observations" in control_matrix)
    require(errors, "control matrix documents image transport media fields", "provider media fields" in control_matrix)
    require(errors, "control matrix documents hidden DOM prevention", "Click-to-reveal values are not embedded in the initial DOM" in control_matrix)
    require(errors, "control matrix documents immediate choice-submit invariant", "choice button" in control_matrix and "immediately submits" in control_matrix and "no separate final submit button" in control_matrix)
    require(errors, "control matrix documents ANTHCU Haiku Computer Use fallback", "computer_20250124" in control_matrix and "computer-use-2025-01-24" in control_matrix and "unavoidable provider/tool-modality difference" in control_matrix)
    require(errors, "control matrix documents max-iteration failure", "`max_iterations` is exhausted" in control_matrix)
    require(errors, "control matrix documents trace action counts", "summary.json` records `action_count`" in control_matrix)

    prompt = read(ROOT / "agents/_shared/prompts.py")
    require(errors, "shared prompt has version stamp", "PROMPT_VERSION" in prompt)
    require(errors, "shared prompt has baseline no-time-limit rule", "There is no time limit." in prompt)
    require(errors, "shared prompt has time-pressure template", "TIME REMAINING" in prompt)
    require(errors, "shared prompt has page-stay guardrail", "Stay on the experiment page." in prompt)
    require(errors, "shared prompt owns no-navigation rule", "Do not navigate away" in prompt)
    require(errors, "shared prompt owns no-search rule", "search the" in prompt)
    require(errors, "shared prompt owns immediate choice-submit rule", "That single click" in prompt and "no separate submit button" in prompt)
    require(errors, "shared prompt prevents post-choice extra actions", "After you click a choice button" in prompt and "look for another submit button" in prompt)
    require(errors, "shared prompt forbids batched tool calls", "Use at most one tool call or UI action in each response." in prompt)

    trace_logger = read(ROOT / "agents/_shared/trace_logger.py")
    require(errors, "trace logger records prompt version", "PROMPT_VERSION" in trace_logger and '"prompt_version": PROMPT_VERSION' in trace_logger)
    require(errors, "trace summary records throttle sleep", '"throttle_sleep_seconds"' in trace_logger)
    require(errors, "trace summary records decision seconds", '"decision_seconds"' in trace_logger)
    require(errors, "trace summary subtracts throttle", "wall_seconds - throttle_sleep_seconds" in trace_logger)
    require(errors, "trace summary records action_count", '"action_count": self.action_count' in trace_logger)
    require(errors, "trace logger writes plan and observation", '"plan": _json_safe(record.get("plan"))' in trace_logger and '"observation": _json_safe(record.get("observation"))' in trace_logger)
    require(errors, "trace logger normalizes singular actions", "def _normalize_action" in trace_logger)
    require(errors, "trace logger extracts browser-use action names", '"interacted_element"' in trace_logger and "action_keys" in trace_logger)
    require(errors, "trace logger accepts explicit trace-only screenshots", 'record.get("screenshot_path")' in trace_logger)
    require(errors, "trace logger honors explicit missing screenshots", 'if "screenshot_path" in record' in trace_logger and "latest_shot = _latest_screenshot()" in trace_logger)
    require(errors, "trace logger redacts nested image data recursively", 'blocks.startswith("data:image")' in trace_logger and "def _looks_like_image_source" in trace_logger)
    require(errors, "trace logger always writes token_usage fallback row", "def _token_usage_fallback_record" in trace_logger and "else:" in trace_logger and "self.record_token_usage(_token_usage_fallback_record(token_totals))" in trace_logger)
    require(errors, "token usage rows include status field", 'entry.setdefault("token_usage_status"' in trace_logger)

    action_audit = read(ROOT / "analysis/audit_action_effects.py")
    require(errors, "action audit classifies GOMS-style operators", "def operator_class" in action_audit and "motor_click" in action_audit and "observe_ops" in action_audit)

    smoke_runner = read(ROOT / "run_smoke_c1_other5.ps1")
    require(
        errors,
        "C1 smoke summary surfaces prompt and token ledger audit fields",
        "PromptVersion" in smoke_runner
        and "TokenUsageStatus" in smoke_runner
        and "TokenUsageRows" in smoke_runner,
    )

    for code, path in LOOPS.items():
        text = read(path)
        require(errors, f"{code} imports shared prompts", "from shared.prompts import" in text)
        require(errors, f"{code} has time_limit_seconds path", "time_limit_seconds" in text)
        require(errors, f"{code} has turn_sleep_seconds path", "turn_sleep_seconds" in text)
        require(errors, f"{code} records throttle sleep", "throttle_sleep_seconds" in text)
        require(errors, f"{code} default model is expected", EXPECTED_DEFAULT_MODELS[code] in text)
        require(errors, f"{code} DEFAULT_MAX_ITERATIONS is 80", assigned_constant(path, "DEFAULT_MAX_ITERATIONS") == 80)
        require(errors, f"{code} DEFAULT_MAX_WALL_SECONDS is 30 min", "DEFAULT_MAX_WALL_SECONDS = 30 * 60" in text)

    for code, expected in EXPECTED_DIRECT_MAX_TOKENS.items():
        require(errors, f"{code} max token budget is 20000", expected in read(LOOPS[code]))

    for code, expected in EXPECTED_DIRECT_TEMPERATURE.items():
        require(errors, f"{code} deterministic temperature constant is 0.0", expected in read(LOOPS[code]))

    for code in ["ANTHCU", "OAICU", "GEMCU"]:
        text = read(LOOPS[code])
        require(errors, f"{code} uses shared baseline prompt directly", "system_prompt = SYSTEM_PROMPT" in text)
        require(errors, f"{code} uses shared time-pressure template directly", "TIME_PRESSURE_SYSTEM_PROMPT_TEMPLATE.format" in text)

    for code in ["ANTHCU", "CLDCR"]:
        text = read(LOOPS[code])
        require(errors, f"{code} strips nested Anthropic tool_result screenshots", "def has_image(block)" in text and "def strip_images(block)" in text)
        require(errors, f"{code} preserves text placeholder after stripping tool_result image", 'block.get("type") == "tool_result"' in text and "(screenshot removed)" in text)

    anthcu = read(LOOPS["ANTHCU"])
    require(errors, "ANTHCU keeps Sonnet on computer_20251124", 'COMPUTER_TOOL_TYPE = "computer_20251124"' in anthcu and 'COMPUTER_USE_BETA = "computer-use-2025-11-24"' in anthcu)
    require(errors, "ANTHCU supports Haiku computer_20250124 fallback", 'HAIKU_COMPUTER_TOOL_TYPE = "computer_20250124"' in anthcu and 'HAIKU_COMPUTER_USE_BETA = "computer-use-2025-01-24"' in anthcu)
    require(errors, "ANTHCU selects Computer Use spec by model", "def computer_use_spec_for_model" in anthcu and "computer_use_spec_for_model(model)" in anthcu)
    require(errors, "ANTHCU passes selected tool type and beta", 'default_headers={"anthropic-beta": computer_use_beta}' in anthcu and '"type": computer_tool_type' in anthcu)
    require(errors, "TraceLogger records Anthropic Computer Use spec", '"computer_tool_type": safe_action.get("computer_tool_type")' in trace_logger and '"computer_use_beta": safe_action.get("computer_use_beta")' in trace_logger)

    cldcr = read(LOOPS["CLDCR"])
    require(errors, "CLDCR enforces one action per screenshot", "MAX_ACTIONS_PER_SCREENSHOT = 1" in cldcr and "skipped_batched_actions" in cldcr)
    require(errors, "CLDCR returns skipped tool results for batched tool_use", "SKIPPED: one-action-per-screenshot gate" in cldcr)
    require(errors, "CLDCR repeated-action guard includes click_at x/y", 'tool_input.get("x")' in cldcr and 'tool_input.get("y")' in cldcr)
    require(errors, "CLDCR disables parallel Anthropic tool use", '"disable_parallel_tool_use": True' in cldcr)

    for code in ["CLDCR", "LCPW"]:
        text = read(LOOPS[code])
        require(errors, f"{code} starts from shared base prompt", "SYSTEM_PROMPT = _BASE_PROMPT + TOOL_SECTION_HEADER" in text)
        require(errors, f"{code} time-pressure keeps tool-only extension", "+ TOOL_SECTION_HEADER" in text)

    brouse = read(LOOPS["BROUSE"])
    require(errors, "BROUSE injects shared prompt via extend_system_message", '"extend_system_message": extend_msg' in brouse)
    require(errors, "BROUSE session_init records prompt version", '"prompt_version": PROMPT_VERSION' in brouse)
    require(errors, "BROUSE task suffix is limited to URL hint", "The experiment page is already open at this URL" in brouse)
    require(errors, "BROUSE does not duplicate time-pressure prompt in task body", "DETAILED TASK" not in brouse)
    require(errors, "BROUSE limits browser-use to one action per step", '"max_actions_per_step": 1' in brouse)
    require(errors, "BROUSE persists browser-use state screenshots", "def _history_actions_with_screenshots" in brouse and '"screenshot_path": screenshot_path' in brouse)

    oaicu = read(LOOPS["OAICU"])
    require(errors, "OAICU has GA tool payload branch", "if tool_type == \"computer\"" in oaicu)
    require(errors, "OAICU GA branch omits dimensions", 'return [{"type": "computer"}]' in oaicu)
    require(errors, "OAICU legacy branch keeps dimensions", '"display_width": MODEL_WIDTH' in oaicu and '"display_height": MODEL_HEIGHT' in oaicu)
    require(errors, "OAICU reasoning effort controlled", '"reasoning"' in oaicu and "DEFAULT_REASONING_EFFORT" in oaicu)
    require(errors, "OAICU refreshes instructions even with previous_response_id", 'kwargs["instructions"] = system_prompt' in oaicu and "if previous_response_id is not None" in oaicu)
    require(errors, "OAICU enforces one action per screenshot", "MAX_ACTIONS_PER_SCREENSHOT = 1" in oaicu and "skipped_batched_actions" in oaicu)

    gemcu = read(LOOPS["GEMCU"])
    require(errors, "GEMCU supports Vertex AI auth mode", "GOOGLE_GENAI_USE_VERTEXAI" in gemcu and "vertexai=True" in gemcu)
    require(errors, "GEMCU Vertex auth requires project/location", "GOOGLE_CLOUD_PROJECT" in gemcu and "GOOGLE_CLOUD_LOCATION" in gemcu)
    require(errors, "GEMCU sends screenshots as FunctionResponse media parts", "FunctionResponsePart" in gemcu and "FunctionResponseBlob" in gemcu)
    require(errors, "GEMCU does not send screenshots as base64 JSON text", 'payload["screenshot"]' not in gemcu and 'response["screenshot"]' not in gemcu)

    direct = ["ANTHCU", "OAICU", "GEMCU", "CLDCR"]
    for code in direct:
        text = read(LOOPS[code])
        require(errors, f"{code} sleeps only after first turn", "if turn > 1 and turn_sleep_seconds > 0" in text)
        require(errors, f"{code} excludes throttle from deadline", "decision_elapsed = elapsed - result.throttle_sleep_seconds" in text)
        require(errors, f"{code} fails runs that exhaust max iterations", "max iterations ({max_iterations}) reached" in text)

    for code in ["ANTHCU", "CLDCR"]:
        text = read(LOOPS[code])
        require(errors, f"{code} has repeated-action runaway guard", "REPEATED_ACTION_GUARD_LIMIT = 8" in text and "repeated identical action" in text)

    for code in ["BROUSE", "LCPW"]:
        text = read(LOOPS[code])
        require(errors, f"{code} callback skips first LLM call", "if self.call_count == 1" in text)
        require(errors, f"{code} callback tracks decision elapsed", "def decision_elapsed" in text)
        require(errors, f"{code} callback overrides LangChain chat start hook", "await _ThrottleCallback.on_chat_model_start" in text)
        require(errors, f"{code} callback overrides LangChain llm start hook", "await _ThrottleCallback.on_llm_start" in text)
        require(errors, f"{code} max token budget set", "20000" in text)
        require(errors, f"{code} deterministic temperature set", "temperature=0.0" in text)

    lcpw = read(LOOPS["LCPW"])
    require(errors, "LCPW session_init records prompt version", '"prompt_version": PROMPT_VERSION' in lcpw)
    require(errors, "LCPW normalizes intermediate steps into actions", "def _step_to_action_record" in lcpw and '"actions": [action_record]' in lcpw)
    require(errors, "LCPW preserves plan and observation in trace", '"plan": action_record.get("plan")' in lcpw and '"observation": action_record.get("observation")' in lcpw)
    require(errors, "LCPW captures trace-only screenshots outside model input", "def _build_async_screenshot_handler" in lcpw and "page.screenshot" in lcpw and '"screenshot_path"' in lcpw)
    require(errors, "LCPW captures screenshots only for click actions", 'tool_name == "click_element"' in lcpw)
    require(errors, "LCPW propagates screenshot callback through agent invoke", 'config={"callbacks": [screenshot_handler]}' in lcpw)
    require(
        errors,
        "LCPW trims old DOM/text observations from model scratchpad",
        "LCPW_DOM_OBSERVATION_TOOL_NAMES" in lcpw
        and "LCPW_EXTRACT_TEXT_OBSERVATION_WINDOW" in lcpw
        and "LCPW_GET_ELEMENTS_OBSERVATION_WINDOW" in lcpw
        and "LCPW_DOM_OBSERVATION_WINDOWS" in lcpw
        and "def _trim_lcpw_intermediate_steps" in lcpw
        and '"trim_intermediate_steps": _trim_lcpw_intermediate_steps' in lcpw,
    )
    require(errors, "LCPW hides navigation tools after pre-navigation", "LCPW_ALLOWED_TOOL_NAMES" in lcpw and "tool for tool in tools" in lcpw)
    require(errors, "LCPW fails max-iteration stops", "max iterations" in lcpw and 'result.stop_reason = "max_iterations"' in lcpw)

    for code, path in RUN_SESSIONS.items():
        text = read(path)
        require(errors, f"{code} CLI supports --time-limit-seconds", "--time-limit-seconds" in text)
        require(errors, f"{code} CLI supports --turn-sleep-seconds", "--turn-sleep-seconds" in text)
        require(errors, f"{code} passes throttle to trace summary", "throttle_sleep_seconds=result.throttle_sleep_seconds" in text)
        require(errors, f"{code} writes shared trace summary", "tracer.write_summary" in text)
        require(errors, f"{code} passes token usage records to trace summary", "token_usage_records=token_usage_records" in text)
        require(errors, f"{code} emits token usage status in stdout JSON", "token_usage_status" in text)

    for code in ["BROUSE", "LCPW"]:
        text = read(LOOPS[code])
        require(errors, f"{code} attaches LangChain token usage handler", "build_langchain_usage_handler" in text and "usage_handler" in text)
        require(errors, f"{code} applies callback token records to result", "apply_token_records_to_result" in text)

    for code in ["OAICU", "GEMCU"]:
        text = read(RUN_SESSIONS[code])
        require(
            errors,
            f"{code} uses normalized uncached tokens with user-entered rates",
            'uncached_input_tokens = token_totals["input_tokens_uncached"]' in text
            and "estimate_usage(token_totals, args.agent_model)" in text
            and "result.cache_read_tokens" in text
            and "result.cache_creation_tokens" in text,
        )

    for code, path in COMPOSE.items():
        text = read(path)
        require(errors, f"{code} compose WIDTH=1920", compose_env_value(text, "WIDTH") == "1920")
        require(errors, f"{code} compose HEIGHT=1200", compose_env_value(text, "HEIGHT") == "1200")

    for code in ["ANTHCU", "OAICU", "GEMCU"]:
        text = read(COMPOSE[code])
        require(errors, f"{code} compose MODEL_WIDTH=1280", compose_env_value(text, "MODEL_WIDTH") == "1280")
        require(errors, f"{code} compose MODEL_HEIGHT=800", compose_env_value(text, "MODEL_HEIGHT") == "800")

    gemcu_compose = read(COMPOSE["GEMCU"])
    require(errors, "GEMCU compose passes Vertex AI flag", "GOOGLE_GENAI_USE_VERTEXAI" in gemcu_compose)
    require(errors, "GEMCU compose passes Vertex project/location", "GOOGLE_CLOUD_PROJECT" in gemcu_compose and "GOOGLE_CLOUD_LOCATION" in gemcu_compose)
    require(errors, "GEMCU compose mounts secrets read-only", "../../secrets:/workspace/secrets:ro" in gemcu_compose)

    experiment_template = read(ROOT / "templates/experiment.html")
    tracker_js = read(ROOT / "static/tracker.js")
    app_py = read(ROOT / "app.py")
    require(errors, "experiment template does not embed hidden cell values", "data-value" not in experiment_template and "{{ cell_value }}" not in experiment_template)
    require(errors, "tracker reveals values via server endpoint", 'fetch("/reveal"' in tracker_js and "revealCell" in tracker_js)
    require(errors, "tracker logs choice_select before submit", '"choice_select"' in tracker_js and "lastChoiceLog" in tracker_js)
    require(
        errors,
        "server validates logged choice state",
        "unsupported event_type" in app_py
        and "choice not in condition" in app_py
        and "valid_choices" in app_py,
    )
    require(
        errors,
        "server validates submitted final choice",
        "final_choice not in condition" in app_py
        and "payload = get_experiment_payload(condition_id)" in app_py,
    )
    require(errors, "server owns reveal lookup and logging", '@app.route("/reveal", methods=["POST"])' in app_py and "get_reveal_value" in app_py and '"event_type": "info_view"' in app_py)

    c12_cost_probe = read(ROOT / "run_c12_cost_probe.ps1")
    require(errors, "C12 cost probe default sleep is 40", "[int]$TurnSleepSeconds = 40" in c12_cost_probe)

    for script in ROOT.glob("run_*time_pressure.ps1"):
        text = read(script)
        require(errors, f"{script.name} sets TurnSleepSeconds=40", "$TurnSleepSeconds   = 40" in text)
        require(errors, f"{script.name} passes --turn-sleep-seconds", "--turn-sleep-seconds $TurnSleepSeconds" in text)
        require(errors, f"{script.name} loads task_prompt.txt", "task_prompt.txt" in text and "Get-Content" in text)
        require(errors, f"{script.name} uses UTF8 task load", "-Encoding UTF8" in text)

    for script in ROOT.glob("run_*all_conditions.ps1"):
        text = read(script)
        require(errors, f"{script.name} sets TurnSleepSeconds=40", "$TurnSleepSeconds = 40" in text or "$TurnSleepSeconds   = 40" in text)
        require(errors, f"{script.name} passes --turn-sleep-seconds", "--turn-sleep-seconds $TurnSleepSeconds" in text)
        require(errors, f"{script.name} loads task_prompt.txt", "task_prompt.txt" in text and "Get-Content" in text)
        require(errors, f"{script.name} uses UTF8 task load", "-Encoding UTF8" in text)

    smoke_other5 = read(ROOT / "run_smoke_c1_other5.ps1")
    require(errors, "C1 smoke script can isolate LCPW", "-OnlyLcpw" in smoke_other5)
    require(errors, "C1 smoke script can isolate BROUSE", "-OnlyBrouse" in smoke_other5)
    require(errors, "C1 smoke script can isolate CLDCR", "-OnlyCldcr" in smoke_other5)
    require(errors, "C1 smoke summary reports action count", "ActionCount" in smoke_other5 and "Get-TraceStats" in smoke_other5)

    if errors:
        print("Experimental-control verification FAILED:")
        for err in errors:
            print(f"- {err}")
        return 1

    print("Experimental-control verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
