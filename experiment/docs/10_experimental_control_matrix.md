# Experimental control matrix

Shared experimental settings for the six agent implementations.

## 원칙

이 프로젝트는 실험이다. 따라서 framework/tool 차이 때문에 unavoidable한 부분을 제외하고는 6개 프레임워크의 설정을 같게 유지한다.

동일해야 하는 것:

- 실험 task text: `task_prompt.txt`
- 공통 system prompt의 실험 지시: `agents/_shared/prompts.py`
- temperature 또는 그에 준하는 deterministic control
- max token/output token budget
- time-pressure deadline 의미
- rate-limit throttle 의미
- viewport/좌표 invariant
- Layer-1/Layer-2 logging schema

달라도 되는 것:

- tool schema와 tool mechanics 설명
- screenshot 입력 여부
- pixel action vs DOM/CDP action
- provider별 API parameter 이름
- 라이브러리 내부 loop 때문에 생기는 per-turn prompt refresh 가능 여부

## Prompt policy

실험 지시는 `agents/_shared/prompts.py`를 source of truth로 둔다.

- ANTHCU/OAICU/GEMCU: 공용 prompt 그대로 사용.
- CLDCR/LCPW: 공용 prompt + `TOOL_SECTION_HEADER` + tool 사용법만 추가.
- BROUSE: browser-use 내장 system prompt 위에 `extend_system_message`로 공용 prompt 추가. task body에는 "이미 실험 페이지가 열려 있다"는 라이브러리 운영 힌트만 추가한다.
- Time-pressure prompt는 가능한 system/instructions 경로에서만 전달한다. user task body에 같은 시간압박 지시를 중복으로 붙이지 않는다.
- 직접 loop(ANTHCU/OAICU/GEMCU/CLDCR)는 매 API 호출마다 time-pressure prompt의 `TIME REMAINING` 값을 갱신한다.

금지:

- 특정 프레임워크에만 "더 열심히 탐색하라", "시간을 아껴라", "새 탭 금지" 같은 실험 지시를 별도로 추가.
- 공용 prompt에 넣어야 할 실험 지시를 개별 loop.py에만 넣기.

### Choice-submit invariant

The experiment page has no separate final submit button. Clicking one apartment choice button (`A`, `B`, etc.) logs `choice_select` and immediately submits the final decision through `/submit`.

This rule is part of the shared experimental prompt in `agents/_shared/prompts.py` and must stay identical across all frameworks. Framework-specific tool text may only translate it into selector or click mechanics, for example LCPW's `[data-choice='A']` selector hint.

### ANTHCU Computer Use tool-version policy

ANTHCU must keep `claude-sonnet-4-6` on Anthropic Computer Use `computer_20251124` with beta header `computer-use-2025-11-24`.

ANTHCU may use `computer_20250124` with beta header `computer-use-2025-01-24` for `claude-haiku-4-5`, because Anthropic rejects `computer_20251124` for that model and documents the older Computer Use beta as the supported Haiku 4.5 path. This is an unavoidable provider/tool-modality difference, not a prompt or task difference.

Layer-2 action traces should record the selected `computer_tool_type` and `computer_use_beta` for audit.

## Hyperparameter policy

| 항목 | 표준 |
|---|---|
| temperature | provider/framework policy에 따름. ANTHCU는 thinking-compatible path 유지를 위해 요청 payload에서 생략 |
| ANTHCU thinking | 기본 `auto`: Opus/Sonnet은 adaptive thinking, Haiku는 manual thinking budget. 끄려면 `--thinking-mode off` |
| OpenAI GPT-5 reasoning | `reasoning.effort = medium`, temperature 미적용 |
| max tokens | `20000` |
| max iterations | run_session default 80, batch scripts usually 200 |
| throttle | 40s in all-condition and time-pressure runners |

Provider-specific parameter names:

- Anthropic ANTHCU: `temperature` omitted, `max_tokens=20000`
- OpenAI Responses: `max_output_tokens=20000`; GPT-5 reasoning uses `reasoning.effort`
- Gemini: `temperature=0.0`, `max_output_tokens=20000`
- LangChain wrappers: `temperature=0.0`, `max_tokens=20000` or `max_output_tokens=20000`

## Current model matrix

Default model settings:

| Framework | Current model setting |
|---|---|
| ANTHCU | `claude-sonnet-4-6` |
| OAICU | `gpt-5.4-mini` |
| GEMCU | `gemini-3-flash-preview` |
| CLDCR | `claude-haiku-4-5` |
| BROUSE | `gpt-5.4-mini`; also run a separate `claude-haiku-4-5` variant |
| LCPW | `claude-haiku-4-5` |

BROUSE's two model settings are explicit model variants, not a single pooled condition. Keep their subject IDs, trace folders, run summaries, and analysis labels separated by full model name.

## Provider auth policy

Provider auth may differ, but it must not change experimental settings.

- GEMCU supports both Gemini Developer API key auth and Vertex AI ADC/service-account auth.
- Developer API mode: `GEMINI_API_KEY` or `GOOGLE_API_KEY`.
- Vertex AI mode: `GOOGLE_GENAI_USE_VERTEXAI=true`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, and Application Default Credentials such as `GOOGLE_APPLICATION_CREDENTIALS=/workspace/secrets/<service-account>.json`.
- Service account JSON files live under `secrets/` and are never committed.
- Switching auth mode must not alter model prompt, temperature, token budget, viewport, throttle, or logging schema.

## Time-pressure policy

Source: `docs/08_time_pressure_throttle.md`.

- Time limit is 180s of decision time.
- Rate-limit sleep is excluded from decision time.
- First API/LLM call has no sleep.
- Later calls sleep 40s when runners pass `--turn-sleep-seconds 40`.
- BROUSE and LCPW attach LangChain async callbacks to their library-internal LLM calls. The concrete handler must override `on_chat_model_start` and `on_llm_start` directly so LangChain base handler methods cannot swallow the throttle.
- `summary.json` records `wall_seconds`, `throttle_sleep_seconds`, `decision_seconds`.

## Viewport and coordinate policy

- Real browser display target: 1920×1200.
- Pixel model coordinate target where applicable: 1280×800.
- Aspect ratio invariant: 16:10 throughout.
- C12 must fit without clipping.
- Pixel frameworks downscale screenshots to model space and upscale actions to real space.

## Information reveal policy

- Click-to-reveal values are not embedded in the initial DOM.
- `templates/experiment.html` may expose only the apartment id, dimension id, and placeholder text before a cell is opened.
- `static/tracker.js` must request `/reveal` after an info-cell click; the server returns the value and logs `info_view`.
- `static/tracker.js` must log `choice_select` immediately when an A/B choice button is clicked. `/submit` still logs `final_choice` and `session_end` after a successful submit.
- `/log` must accept only `session_start` and `choice_select`; `/submit` must accept only final choices that belong to the current condition. Malformed choices must return JSON 400 and must not be logged.
- DOM-oriented frameworks such as BROUSE and LCPW must not be able to recover hidden values through `textContent`, `outerHTML`, accessibility text, or element attributes before clicking.

## Action granularity policy

- The shared system prompt tells every framework: use at most one tool call or UI action in each response, do not batch clicks/tool calls, and wait for the next screenshot/observation before acting again.
- A screenshot authorizes exactly one UI action.
- If a provider can return batched UI actions from one screenshot, the runner must execute only the first executable action and then return a fresh screenshot.
- OAICU/GPT-5.5 GA can return `computer_call.actions[]`; OAICU enforces this with `MAX_ACTIONS_PER_SCREENSHOT = 1`.
- CLDCR/Anthropic custom tools can return multiple `tool_use` blocks in one assistant turn; CLDCR executes only the first and returns skipped text tool results for the rest so the Anthropic tool protocol remains valid.
- BROUSE/browser-use can plan multiple actions in one step; the wrapper sets `max_actions_per_step=1` so one model observation produces at most one executed browser action.
- Skipped batched OAICU actions are audit metadata only; they are not written as executed actions in `actions.jsonl`.
- Direct loop runners must not report success when `max_iterations` is exhausted before a normal stop.
- ANTHCU and CLDCR guard against repeated identical successful actions so a stuck click loop fails early instead of burning the full iteration budget.
- LCPW pre-navigates to the experiment URL before the model starts, so navigation tools are not exposed to the model. Its tool allowlist is DOM read tools plus the custom `click_element` tool.

## Image transport policy

- Image bytes must travel through provider media fields, not as ordinary JSON/text payload.
- OAICU: `image_url` media fields (`input_image` or GA `computer_screenshot`) may contain a `data:image/png;base64,...` URL.
- GEMCU: screenshots must use `FunctionResponsePart(inline_data=FunctionResponseBlob(...))`; `response["screenshot"]` base64 JSON is forbidden.
- ANTHCU/CLDCR: screenshots use Anthropic image blocks with `source.type="base64"`, `media_type="image/png"`, and `data`.
- LCPW: no screenshot or image block is sent to the model; Playwright screenshots may be saved only as trace artifacts after click actions.
- Trace logs redact image payloads recursively; long raw base64 or `data:image...` strings should not appear anywhere in `messages.jsonl`.

## Screenshot and DOM pruning policy

- Direct screenshot-based loops keep only the latest user-side screenshot in the model payload.
- ANTHCU and CLDCR must strip both top-level image blocks and nested `tool_result.content[]` image blocks from older user turns.
- OAICU and GEMCU must strip provider-specific screenshot media shapes from older turns.
- LCPW is DOM-only and does not send screenshots to the model. Its `screenshots/turn_*.png` files are click-action audit/debug artifacts referenced by `messages.jsonl`, not model input.
- LCPW trims old DOM/text intermediate observations from the model scratchpad with a callable `trim_intermediate_steps`; by default only the latest `extract_text` observation is kept, while the latest five `get_elements` observations are kept for selector recovery. Recent non-DOM action outcomes use `LCPW_INTERMEDIATE_STEP_WINDOW`. Full intermediate steps are still returned after the run and written to Layer-2 traces.
- BROUSE delegates model vision context management to the browser-use library. The wrapper decodes the browser-use `BrowserStateHistory.screenshot` for each step into trace-only PNG files and links them from `messages.jsonl`; raw image payloads are not copied into the JSONL logs.

## Trace counting policy

- `iterations` is the runner/library loop count and is not always the same kind of event across frameworks.
- `actions.jsonl` is the durable executed/tool-action ledger and should be used for cross-framework action counts when available.
- `summary.json` records `action_count` as the number of action rows emitted by the shared trace logger.
- DOM-oriented frameworks may have read-only actions (`extract_text`, `get_elements`, `current_webpage`) that are not UI-changing clicks.
- KLM/GOMS-style operator summaries are diagnostic only. Coarse classes such as `observe`, `motor_click`, `motor_type`, `navigation`, and `done` help reveal method/tool-surface differences, but they are not direct human execution-time predictions.
- LCPW records LangChain intermediate steps as normalized action records with `plan`, `input`, `output`/`observation`, and `library`.
- BROUSE records `browser-use` model actions as named action records such as `go_to_url`, `click_element_by_index`, and `done`.

## Runtime audit stamp policy

- `agents/_shared/prompts.py` owns `PROMPT_VERSION`.
- `summary.json`, `messages.jsonl`, and `token_usage.jsonl` must record `prompt_version`.
- `token_usage.jsonl` must exist for every fresh trace folder. If exact per-call usage is unavailable, it contains one fallback row with `token_usage_status=unavailable` or `legacy_summary_only`.
- A batch summary should surface `PromptVersion` and `TokenUsageRows` so stale Docker images can be detected without manually opening each trace folder.

## Verification command

Run this after prompt/hyperparameter/time-pressure edits:

```powershell
python tools\verify_experimental_controls.py
```

The verifier is static. Passing it does not prove runtime API compatibility, but failing it means experimental-control drift needs attention before running data collection.
