# Agent Implementations

All runners interact with the same Flask task. Shared prompts and logging live in [_shared/](_shared/README.md); `registry.py` defines framework codes and subject IDs.

## Choose a Runner

- [ANTHCU — Anthropic Computer Use](computer_use/README.md): screenshots → pixel actions → X11.
- [OAICU — OpenAI Computer Use](openai_computer_use/README.md): screenshots → computer-tool actions → X11.
- [GEMCU — Gemini Computer Use](gemini_computer_use/README.md): screenshots → computer-use actions → X11.
- [CLDCR — Custom CDP Tools](claude_in_chrome/README.md): screenshots → custom tools → Playwright/CDP.
- [BROUSE — browser-use](browser_use/README.md): vision/DOM → library actions → Playwright/CDP.
- [LCPW — LangChain Playwright](langchain_playwright/README.md): DOM/text → selector tools → Playwright/CDP.

## Credentials

From the repository root, copy `.env.example` to `.env`. Replace `your_API_key_here` only for the provider you use. Keep unused keys empty or as placeholders. Load keys into the current PowerShell process:

```powershell
. .\tools\load_api_keys.ps1
```

The loader accepts only known credential/configuration names, skips placeholders, and never prints values. Compose forwards environment variables from the current shell; a root `.env` is not automatically loaded when running Compose inside a framework directory. Do not use `docker compose config` with real credentials: expanded configuration can expose values.

Anthropic runners require `ANTHROPIC_API_KEY`; OpenAI requires `OPENAI_API_KEY`; Gemini accepts `GEMINI_API_KEY` or `GOOGLE_API_KEY`. BROUSE/LCPW need the key matching the selected model. Gemini also supports optional Vertex AI credentials; put any service-account file in ignored `secrets/` and configure the container-visible path.

## Build and Observe

Start Flask in a separate terminal. Build the [shared base image](_base/README.md), then build your selected framework. These commands install software inside Docker images; execute them only when you intend to prepare the runtime.

Live observation uses `docker compose run --rm --service-ports ...` and `http://localhost:6080/vnc.html`. The six Compose services share port 6080, so run them sequentially.

## Controls and Traces

Use the exact UTF-8 root `task_prompt.txt`; experimental instructions are in `_shared/prompts.py`. Standard runners retain 40-second throttling after the first LLM call, 20,000-token output limits, and the common display policy. Standalone CLI defaults can differ: explicitly pass `--turn-sleep-seconds 40` when reproducing the standard setup.

Subject IDs are `FRAMEWORK__full-model-id__run-token`. Layer-1 behavior is written by Flask to `logs/`; Layer-2 traces go to `logs/agent_traces/<slug>/<model>/<subject>_<condition>_<timestamp>/`. Trace files include `summary.json`, `actions.jsonl`, `messages.jsonl`, `token_usage.jsonl`, and available screenshots. LCPW screenshots are audit artifacts, not model input.

Do not equate process exit 0 with success. Check a normal stop, no error, completion, prompt version, and timing/token fields. Failed IDs must not be reused.

See [costs](../costs/README.md), [runner details](../docs/runners/README.md), and [experimental controls](../docs/10_experimental_control_matrix.md). Provider access and current SDK/model availability must be checked with a new-subject smoke before a batch.
