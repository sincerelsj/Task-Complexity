# Shared Experimental Modules

- `prompts.py`: versioned experimental instructions, one-action policy, and time-pressure text.
- `trace_logger.py`: normalized actions/messages, screenshot references, summaries, and image-payload redaction.
- `pricing.py`: optional user-entered rates; missing rates produce unavailable cost estimates.
- `token_accounting.py`: provider/callback normalization into per-call token records.

`decision_seconds = wall_seconds - throttle_sleep_seconds`. The first LLM call has no rate-limit sleep; later standard calls sleep 40 seconds. Deadline logic uses decision time.

Token-resource analysis requires an `exact*` usage status. `unavailable` means usage was not measured, not that the run consumed zero tokens. Provider-specific raw usage fields have different cache semantics; use normalized fields when calculating costs. See [cost accounting](../../costs/README.md).
