# Timing and Throttling

## Decision Time

```text
decision_seconds = wall_seconds - throttle_sleep_seconds
```

`wall_seconds` is elapsed session time. `throttle_sleep_seconds` is the accumulated rate-limit interval. Time-pressure sessions apply the 180-second limit to `decision_seconds`.

## Call Scheduling

1. The first LLM call starts immediately.
2. Later calls wait for `--turn-sleep-seconds` (40 seconds in the standard runners).
3. The runner records accumulated wait time and checks the decision deadline.

ANTHCU, OAICU, GEMCU, and CLDCR manage this in their direct loops and refresh the remaining-time prompt on each API call. BROUSE and LCPW apply it through callbacks around library-managed calls; their initial prompt contains the time budget, and callbacks enforce the deadline.

For four calls with a 40-second interval, accumulated throttle time is `3 × 40 = 120` seconds.

## Recorded Fields

- `wall_seconds`: total elapsed time.
- `throttle_sleep_seconds`: accumulated waiting time.
- `decision_seconds`: elapsed time with throttling removed.

The fields are written to the Layer-2 session summary. For library-managed loops, LLM-call count and library iteration count can differ; use recorded throttle time directly.

## Configuration

Use the root `run_<framework>_time_pressure.ps1` scripts or pass `--time-limit-seconds` and `--turn-sleep-seconds` to the selected session runner. Docker images contain the Python loop implementation, so rebuild the base and selected framework after changing shared runtime code.
