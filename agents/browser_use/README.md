# BROUSE — browser-use

The browser-use library manages the agent loop and browser observations. max_actions_per_step=1 preserves the common action granularity. Other model variants must remain separate in logging and analysis.

**Default model:** `gpt-5.4-mini`. **Credential:** `OPENAI_API_KEY for the default model`.

## Build

Start Flask and load credentials following the [agent setup](../README.md). For the shared-base requirement, see [_base/](../_base/README.md).

```powershell
Push-Location agents/browser_use
docker compose build
Pop-Location
```

## Single Session

Run from the repository root, after loading keys in this PowerShell process. Choose a fresh run token for every attempt, including failed attempts.

```powershell
$model = "gpt-5.4-mini"
$subject = "BROUSE__${model}__demo_C1_001"
$condition = "C1"
$task = (Get-Content .\task_prompt.txt -Encoding UTF8 -Raw).Trim()
$url = "http://host.docker.internal:5000/experiment?subject=$subject&condition=$condition&participant_type=agent&agent_framework=browser_use&agent_model=$model"
Push-Location agents/browser_use
try {
  docker compose run --rm --service-ports agent `
    --url $url --task $task --subject-id $subject `
    --condition-id $condition --framework-code BROUSE --agent-model $model `
    --max-iterations 200 --max-wall-seconds 1800 --turn-sleep-seconds 40
} finally {
  Pop-Location
}
```

View `http://localhost:6080/vnc.html` during the run. Inspect `summary.json` and the Layer-1 final-choice event before accepting completion.

## Implementation and Cost

`runner/run_session.py` handles CLI settings and session summaries; `runner/loop.py` implements or wraps the agent loop. Tool execution lives alongside the loop where applicable. Shared controls live in `../_shared/`.

Enter your own prices following [cost configuration](../../costs/README.md). No preset model prices are included. Missing prices produce an unavailable estimate; normalized tokens remain recorded.
