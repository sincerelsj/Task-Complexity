# User-Configured Cost Estimates

No model prices are bundled. Enter your own rates in **USD per 1 million tokens**, for the exact model, provider endpoint, and billing tier used by your run. Missing prices mean **not configured**, never free usage.

## Enter Rates

Copy `rates.example.json` to `pricing.local.json` in this folder. Replace `your_full_model_id` with the full API model ID and replace each applicable `null` with your price:

- `input`: uncached input rate.
- `output`: total output rate, including billable reasoning where applicable.
- `cache_write`: cache-creation rate.
- `cache_read`: cache-hit rate.

The example contains no numeric defaults. Local prices are ignored by Git. You may explicitly enter zero if your billing agreement charges nothing for a bucket; no zero is assumed for a nonempty bucket.

## Calculate After a Run

```powershell
python costs/estimate.py --ledger <token_usage.jsonl> --rates costs/pricing.local.json
```

Omit `--rates` to enter rates interactively for each model found in the ledger:

```powershell
python costs/estimate.py --ledger <token_usage.jsonl>
```

## Optional Estimates During Agent Runs

After saving your local prices, run `. .\tools\load_api_keys.ps1` from the repository root. The loader forwards price JSON through `EXPERIMENT_PRICING_JSON`; Compose passes it to all six runners. Only rates for the selected full model ID are used.

The final runner JSON contains `estimated_cost_usd`, `cost_estimate_status`, and the user rates actually used. These fields stay flat for compatibility with batch parsers. Missing or invalid prices yield JSON `null` and an explicit status. Per-call token records remain available independently of pricing.

Batch cost fields use `not_configured` when prices are absent. Calculate totals from normalized token ledgers with the commands above.

## Accounting Rules

Use the normalized `token_usage.jsonl` fields:

```text
estimate = (
    input_tokens_uncached * input_rate
  + output_tokens_total * output_rate
  + cache_creation_tokens * cache_write_rate
  + cache_read_tokens * cache_read_rate
) / 1_000_000
```

Anthropic raw input excludes separately reported cache tokens; OpenAI raw input includes cache hits. Shared normalization handles that distinction. Do not subtract cache tokens again from `input_tokens_uncached` or add reasoning tokens a second time to output totals.

Only `exact*` usage is eligible. Missing usage is not zero. The estimator covers token charges at the rates you enter; long-context tiers, time-based cache storage, tools, regional premiums, taxes, and discounts require separate accounting. Split ledgers by billing tier when rates vary across calls. Provider invoices remain authoritative.
