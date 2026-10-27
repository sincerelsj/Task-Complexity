# Batch and Time-Pressure Runners

PowerShell entry points remain at the repository root because existing verifier rules and run conventions expect them there. The release replaces the author's absolute workspace paths with `$PSScriptRoot`; task wording, models, conditions, throttling, and budgets are retained.

## Standard Entry Points

`run_<framework>_all_conditions.ps1` exists for ANTHCU/OAICU/GEMCU/CLDCR/BROUSE/LCPW. The filename does **not** guarantee that all 12 conditions are enabled: inspect `$Conditions` in the selected file before running it. The ANTHCU script currently selects C1, C6, and C12. This release does not silently expand the study.

`run_<framework>_time_pressure.ps1` provides the existing time-pressure variant. `run_smoke_c1_other5.ps1` and `run_c12_cost_probe.ps1` provide focused maintenance probes. `run_claude_3tier_c1_to_c12.ps1` retains the historical three-tier ANTHCU batch configuration; inspect its parameters before use.

From the repository root:

```powershell
. .\tools\load_api_keys.ps1
.\run_anthcu_all_conditions.ps1
```

Flask must already be running. Start only after preparing the selected Docker image and confirming provider access. Keep `$TurnSleepSeconds = 40` for the standard experiment. A failed subject requires the next unused repetition token, never the same ID.

## Timing

No sleep precedes the first LLM call. Later calls sleep, and accumulated sleep is removed from decision time. The standard time-pressure variant uses 180 seconds of decision time. A wall-clock cap can still interrupt long throttled sessions; inspect it before a batch. See [timing policy](../08_time_pressure_throttle.md).

Check `summary.json`, not only shell exit codes or batch CSVs. Iteration exhaustion after an action is a failed run. Keep raw logs private and locally generated.
