# OAICU — OpenAI Computer Use

Responses API computer-tool observations and pixel actions, executed through X11. At most one executable action is applied before a fresh screenshot.

**Default model:** `gpt-5.4-mini`. **Credential:** `OPENAI_API_KEY`. Defaults describe the bundled implementation, not the full set of models evaluated in the paper.

## Build

Start Flask and load credentials following the [agent setup](../README.md). For the shared-base requirement, see [_base/](../_base/README.md).

```powershell
Push-Location agents/openai_computer_use
docker compose build
Pop-Location
```

## Single Session

Run from the repository root, after loading keys in this PowerShell process. Choose a fresh run token for every attempt, including failed attempts.

```powershell
$model = "gpt-5.4-mini"
$subject = "OAICU__${model}__demo_C1_001"
$condition = "C1"
$task = (Get-Content .\task_prompt.txt -Encoding UTF8 -Raw).Trim()
$url = "http://host.docker.internal:5000/experiment?subject=$subject&condition=$condition&participant_type=agent&agent_framework=openai_computer_use&agent_model=$model"
Push-Location agents/openai_computer_use
try {
  docker compose run --rm --service-ports agent `
    --url $url --task $task --subject-id $subject `
    --condition-id $condition --framework-code OAICU --agent-model $model `
    --max-iterations 200 --max-wall-seconds 1800 --turn-sleep-seconds 40
} finally {
  Pop-Location
}
```

View `http://localhost:6080/vnc.html` during the run. Inspect `summary.json` and the Layer-1 final-choice event before accepting completion.

## Implementation and Cost

`runner/run_session.py` handles CLI settings and session summaries; `runner/loop.py` implements or wraps the agent loop. Tool execution lives alongside the loop where applicable. Shared controls live in `../_shared/`.

Enter your own prices following [cost configuration](../../costs/README.md). No preset model prices are included. Missing prices produce an unavailable estimate; normalized tokens remain recorded.
