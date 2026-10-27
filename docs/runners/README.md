# Batch Runners

PowerShell entry points are located at the repository root and resolve paths through `$PSScriptRoot`.

## Select a Run

- `run_<framework>_all_conditions.ps1`: execute the conditions in the script's `$Conditions` setting. The ANTHCU script selects C1, C6, and C12 by default.
- `run_<framework>_time_pressure.ps1`: run the time-pressure variant.
- `run_smoke_c1_other5.ps1`: focused C1 sessions with framework selection switches.
- `run_c12_cost_probe.ps1`: C12 token/resource collection.
- `run_claude_3tier_c1_to_c12.ps1`: three-tier ANTHCU batch with condition-range parameters.

Framework slugs are `anthcu`, `oaicu`, `gemcu`, `cldcr`, `brouse`, and `lcpw`.

## Execute

Start Flask in one terminal and load provider keys in the runner terminal:

```powershell
. .\tools\load_api_keys.ps1
.\run_anthcu_all_conditions.ps1
```

Prepare the selected Docker image using its [agent guide](../../agents/README.md). Set a new subject/run token for each session. Batch output and agent traces are saved under `logs/`.

## Timing and Completion

Standard runners pass a 40-second interval after the first LLM call. Decision time excludes this interval; the time-pressure variant uses a 180-second decision budget. Wall-clock caps are configured separately in each script.

Completion is represented by a normal provider stop and an error-free session summary. Read `summary.json` and the final-choice event for session outcomes. [Timing settings](../08_time_pressure_throttle.md) describe the recorded fields.
