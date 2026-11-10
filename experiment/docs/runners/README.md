# Example Batch Runner

[run_anthcu_all_conditions.ps1](../../run_anthcu_all_conditions.ps1) runs Anthropic Computer Use for the conditions listed in its `$Conditions` setting. It defaults to C1, C6, and C12 and resolves paths through `$PSScriptRoot`. For other frameworks, use the session commands in the [agent guides](../../agents/README.md).

## Execute

Start Flask in one terminal and load provider keys in the runner terminal:

```powershell
. .\tools\load_api_keys.ps1
.\run_anthcu_all_conditions.ps1
```

Prepare the selected Docker image using its [agent guide](../../agents/README.md). Set a new subject/run token for each session. Batch output and agent traces are saved under `logs/`.

## Timing and Completion

The example passes a 40-second interval after the first LLM call. Decision time excludes this interval. To run a timed session, pass `--time-limit-seconds 180` to the selected session runner. The wall-clock cap is configured separately.

Completion is represented by a normal provider stop and an error-free session summary. Read `summary.json` and the final-choice event for session outcomes. [Timing settings](../08_time_pressure_throttle.md) describe the recorded fields.
