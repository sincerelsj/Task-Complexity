"""Optional user-entered prices. No provider rates are built into the code."""
from __future__ import annotations

import json
import math
import os

BUCKETS = {
    "input_tokens_uncached": "input",
    "output_tokens_total": "output",
    "cache_creation_tokens": "cache_write",
    "cache_read_tokens": "cache_read",
}


def estimate_usage(usage: dict, model: str, rates: dict | None = None) -> dict:
    """Return an explicit unavailable status unless counts and rates suffice."""
    if not str(usage.get("token_usage_status", "")).startswith("exact"):
        return {"status": "usage_unavailable", "usd": None}
    if rates is None:
        try:
            rates = json.loads(os.environ.get("EXPERIMENT_PRICING_JSON", "{}"))
        except (ValueError, TypeError):
            return {"status": "invalid_user_rates", "usd": None}
    if not isinstance(rates, dict):
        return {"status": "invalid_user_rates", "usd": None}
    selected = rates.get(model)
    if not isinstance(selected, dict):
        return {"status": "rates_not_configured", "usd": None}
    total = 0.0
    used_rates = {}
    for count_field, rate_field in BUCKETS.items():
        count = usage.get(count_field, usage.get(count_field + "_total"))
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return {"status": "invalid_token_counts", "usd": None}
        rate = selected.get(rate_field)
        if count == 0 and rate is None:
            continue
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate < 0:
            return {"status": "rates_not_configured", "usd": None}
        total += count * rate / 1_000_000
        used_rates[rate_field] = rate
    return {"status": "estimated_from_user_rates", "usd": total,
            "model": model, "rates_per_million": used_rates}
