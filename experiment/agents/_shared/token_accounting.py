"""Shared token accounting helpers for agent runners.

The project compares agent resource use across heterogeneous frameworks.
Provider SDKs and LangChain wrappers expose token usage in different shapes,
so every runner normalizes usage into the same per-LLM-call record before
writing traces or aggregate summaries.
"""

from __future__ import annotations

from typing import Any, Optional


TOKEN_ACCOUNTING_VERSION = "2026-06-06.v1"


def provider_from_model(model: str) -> str:
    lower = (model or "").lower()
    if lower.startswith(("gpt-", "o1", "o3", "o4", "o5")):
        return "openai"
    if lower.startswith("gemini-"):
        return "google"
    if lower.startswith("claude-"):
        return "anthropic"
    return "unknown"


def usage_from_anthropic(
    usage: Any,
    *,
    model: str,
    turn: int,
    call_index: int,
    usage_source: str = "anthropic_messages",
) -> dict[str, Any]:
    input_uncached = _int(_get(usage, "input_tokens"))
    output_total = _int(_get(usage, "output_tokens"))
    cache_read = _int(_get(usage, "cache_read_input_tokens"))
    cache_creation = _int(_get(usage, "cache_creation_input_tokens"))
    output_details = _get(usage, "output_tokens_details") or {}
    reasoning = (
        _int(_get(output_details, "thinking_tokens"))
        or _int(_get(output_details, "reasoning_tokens"))
        or _int(_get(usage, "reasoning_tokens"))
    )
    visible_output = max(output_total - reasoning, 0)
    input_total = input_uncached + cache_read + cache_creation
    return _usage_record(
        provider="anthropic",
        model=model,
        turn=turn,
        call_index=call_index,
        usage_source=usage_source,
        usage_status="exact_provider",
        input_tokens_total=input_total,
        input_tokens_uncached=input_uncached,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        output_tokens_total=output_total,
        visible_output_tokens=visible_output,
        reasoning_tokens=reasoning,
        legacy_input_tokens=input_uncached,
        raw_usage=usage,
    )


def usage_from_openai(
    usage: Any,
    *,
    model: str,
    turn: int,
    call_index: int,
    usage_source: str = "openai_responses",
) -> dict[str, Any]:
    input_total = _int(_get(usage, "input_tokens"))
    output_total = _int(_get(usage, "output_tokens"))
    input_details = _get(usage, "input_tokens_details") or {}
    output_details = _get(usage, "output_tokens_details") or {}
    cache_read = _int(_get(input_details, "cached_tokens"))
    cache_creation = _int(_get(input_details, "cache_creation_tokens"))
    reasoning = _int(_get(output_details, "reasoning_tokens"))
    input_uncached = max(input_total - cache_read - cache_creation, 0)
    visible_output = max(output_total - reasoning, 0)
    return _usage_record(
        provider="openai",
        model=model,
        turn=turn,
        call_index=call_index,
        usage_source=usage_source,
        usage_status="exact_provider",
        input_tokens_total=input_total,
        input_tokens_uncached=input_uncached,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        output_tokens_total=output_total,
        visible_output_tokens=visible_output,
        reasoning_tokens=reasoning,
        legacy_input_tokens=input_total,
        raw_usage=usage,
    )


def usage_from_gemini(
    usage: Any,
    *,
    model: str,
    turn: int,
    call_index: int,
    usage_source: str = "google_genai",
) -> dict[str, Any]:
    input_total = _int(_get(usage, "prompt_token_count"))
    output_total = _int(_get(usage, "candidates_token_count"))
    cache_read = _int(_get(usage, "cached_content_token_count"))
    reasoning = _int(_get(usage, "thoughts_token_count"))
    input_uncached = max(input_total - cache_read, 0)
    visible_output = max(output_total - reasoning, 0)
    return _usage_record(
        provider="google",
        model=model,
        turn=turn,
        call_index=call_index,
        usage_source=usage_source,
        usage_status="exact_provider",
        input_tokens_total=input_total,
        input_tokens_uncached=input_uncached,
        cache_read_tokens=cache_read,
        cache_creation_tokens=0,
        output_tokens_total=output_total,
        visible_output_tokens=visible_output,
        reasoning_tokens=reasoning,
        legacy_input_tokens=input_total,
        raw_usage=usage,
    )


def usage_from_langchain_payload(
    usage: Any,
    *,
    model: str,
    turn: int,
    call_index: int,
    usage_source: str,
) -> Optional[dict[str, Any]]:
    """Normalize token usage exposed through LangChain result metadata."""
    payload = _unwrap_usage_payload(usage)
    if not payload:
        return None

    if _has_any(payload, "prompt_token_count", "candidates_token_count"):
        record = usage_from_gemini(
            payload,
            model=model,
            turn=turn,
            call_index=call_index,
            usage_source=usage_source,
        )
        record["usage_status"] = "exact_langchain_callback"
        return record

    prompt_tokens = _first_int(
        payload,
        "input_tokens",
        "prompt_tokens",
        "prompt_token_count",
    )
    output_tokens = _first_int(
        payload,
        "output_tokens",
        "completion_tokens",
        "candidates_token_count",
    )
    if prompt_tokens is None and output_tokens is None:
        return None

    input_total = prompt_tokens or 0
    output_total = output_tokens or 0
    input_details = (
        _get(payload, "input_token_details")
        or _get(payload, "input_tokens_details")
        or _get(payload, "prompt_tokens_details")
        or {}
    )
    output_details = (
        _get(payload, "output_token_details")
        or _get(payload, "output_tokens_details")
        or _get(payload, "completion_tokens_details")
        or {}
    )
    cache_read = _first_int(
        input_details,
        "cache_read",
        "cache_read_input_tokens",
        "cached_tokens",
        "cached_content_token_count",
    ) or _first_int(
        payload,
        "cache_read_input_tokens",
        "cached_tokens",
        "cached_content_token_count",
    ) or 0
    cache_creation = _first_int(
        input_details,
        "cache_creation",
        "cache_creation_input_tokens",
        "cache_creation_tokens",
    ) or _first_int(
        payload,
        "cache_creation_input_tokens",
        "cache_creation_tokens",
    ) or 0
    reasoning = _first_int(
        output_details,
        "reasoning",
        "reasoning_tokens",
    ) or _first_int(payload, "reasoning_tokens", "thoughts_token_count") or 0
    input_uncached = max(input_total - cache_read - cache_creation, 0)
    visible_output = max(output_total - reasoning, 0)
    return _usage_record(
        provider=provider_from_model(model),
        model=model,
        turn=turn,
        call_index=call_index,
        usage_source=usage_source,
        usage_status="exact_langchain_callback",
        input_tokens_total=input_total,
        input_tokens_uncached=input_uncached,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        output_tokens_total=output_total,
        visible_output_tokens=visible_output,
        reasoning_tokens=reasoning,
        legacy_input_tokens=input_total,
        raw_usage=payload,
    )


def extract_langchain_usage_payloads(output: Any) -> list[Any]:
    payloads: list[Any] = []
    llm_output = _get(output, "llm_output")
    if llm_output:
        for key in ("token_usage", "usage", "usage_metadata"):
            value = _get(llm_output, key)
            if value:
                payloads.append(value)

    generations = _get(output, "generations") or []
    for group in generations:
        items = group if isinstance(group, list) else [group]
        for generation in items:
            message = _get(generation, "message")
            if message is None:
                message = _get(generation, "text")
            if message is None:
                continue
            usage_metadata = _get(message, "usage_metadata")
            if usage_metadata:
                payloads.append(usage_metadata)
            response_metadata = _get(message, "response_metadata") or {}
            for key in ("usage", "token_usage", "usage_metadata"):
                value = _get(response_metadata, key)
                if value:
                    payloads.append(value)
    return payloads


def build_langchain_usage_handler(model: str):
    """Return an AsyncCallbackHandler that records LangChain token usage."""
    try:
        from langchain_core.callbacks import AsyncCallbackHandler  # type: ignore
    except Exception:
        from langchain.callbacks.base import AsyncCallbackHandler  # type: ignore

    class _UsageHandler(AsyncCallbackHandler):  # type: ignore[misc]
        def __init__(self, model: str):
            super().__init__()
            self.model = model
            self.records: list[dict[str, Any]] = []
            self.call_count = 0
            self._seen_run_ids: set[str] = set()

        async def on_llm_start(self, *args, **kwargs):
            return None

        async def on_chat_model_start(self, *args, **kwargs):
            return None

        async def on_llm_end(self, response, *args, **kwargs):
            self._record(response, "langchain_on_llm_end", kwargs.get("run_id"))

        async def on_chat_model_end(self, response, *args, **kwargs):
            self._record(response, "langchain_on_chat_model_end", kwargs.get("run_id"))

        def _record(self, response: Any, usage_source: str, run_id: Any = None) -> None:
            run_key = str(run_id) if run_id is not None else ""
            if run_key and run_key in self._seen_run_ids:
                return
            payloads = extract_langchain_usage_payloads(response)
            if not payloads:
                return
            self.call_count += 1
            # One model call should produce one usage object. If a wrapper
            # exposes several shapes, keep the most informative one.
            records = []
            for payload in payloads:
                record = usage_from_langchain_payload(
                    payload,
                    model=self.model,
                    turn=self.call_count,
                    call_index=len(self.records) + 1,
                    usage_source=usage_source,
                )
                if record is not None:
                    records.append(record)
            if not records:
                return
            best = max(records, key=lambda r: int(r.get("total_tokens") or 0))
            if run_key:
                self._seen_run_ids.add(run_key)
            self.records.append(best)

    return _UsageHandler(model)


def apply_token_record_to_result(result: Any, record: dict[str, Any]) -> None:
    result.input_tokens += _int(record.get("legacy_input_tokens"))
    result.output_tokens += _int(record.get("output_tokens_total"))
    result.cache_read_tokens += _int(record.get("cache_read_tokens"))
    result.cache_creation_tokens += _int(record.get("cache_creation_tokens"))


def apply_token_records_to_result(result: Any, records: list[dict[str, Any]]) -> None:
    result.input_tokens = 0
    result.output_tokens = 0
    result.cache_read_tokens = 0
    result.cache_creation_tokens = 0
    for record in records:
        apply_token_record_to_result(result, record)


def aggregate_token_usage(
    records: Optional[list[dict[str, Any]]],
    *,
    fallback_input_tokens: int = 0,
    fallback_output_tokens: int = 0,
    fallback_cache_read_tokens: int = 0,
    fallback_cache_creation_tokens: int = 0,
) -> dict[str, Any]:
    rows = records or []
    if rows:
        status_values = {str(r.get("usage_status") or "") for r in rows}
        if status_values == {"exact_provider"}:
            status = "exact_provider"
        elif status_values == {"exact_langchain_callback"}:
            status = "exact_langchain_callback"
        elif status_values <= {"exact_provider", "exact_langchain_callback"}:
            status = "exact_mixed"
        else:
            status = "ambiguous_mapping"
        input_total = sum(_int(r.get("input_tokens_total")) for r in rows)
        input_uncached = sum(_int(r.get("input_tokens_uncached")) for r in rows)
        output_total = sum(_int(r.get("output_tokens_total")) for r in rows)
        visible_output = sum(_int(r.get("visible_output_tokens")) for r in rows)
        reasoning = sum(_int(r.get("reasoning_tokens")) for r in rows)
        cache_read = sum(_int(r.get("cache_read_tokens")) for r in rows)
        cache_creation = sum(_int(r.get("cache_creation_tokens")) for r in rows)
        legacy_input = sum(_int(r.get("legacy_input_tokens")) for r in rows)
    elif fallback_input_tokens or fallback_output_tokens or fallback_cache_read_tokens or fallback_cache_creation_tokens:
        status = "legacy_summary_only"
        legacy_input = fallback_input_tokens
        cache_read = fallback_cache_read_tokens
        cache_creation = fallback_cache_creation_tokens
        input_total = fallback_input_tokens + cache_read + cache_creation
        input_uncached = fallback_input_tokens
        output_total = fallback_output_tokens
        visible_output = fallback_output_tokens
        reasoning = 0
    else:
        status = "unavailable"
        legacy_input = 0
        input_total = 0
        input_uncached = 0
        output_total = 0
        visible_output = 0
        reasoning = 0
        cache_read = 0
        cache_creation = 0

    return {
        "token_accounting_version": TOKEN_ACCOUNTING_VERSION,
        "token_usage_status": status,
        "token_usage_calls": len(rows),
        "input_tokens_total": input_total,
        "input_tokens_uncached": input_uncached,
        "legacy_input_tokens": legacy_input,
        "output_tokens_total": output_total,
        "visible_output_tokens": visible_output,
        "reasoning_tokens": reasoning,
        "cache_read_tokens_total": cache_read,
        "cache_creation_tokens_total": cache_creation,
        "total_tokens": input_total + output_total,
    }


def _usage_record(
    *,
    provider: str,
    model: str,
    turn: int,
    call_index: int,
    usage_source: str,
    usage_status: str,
    input_tokens_total: int,
    input_tokens_uncached: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    output_tokens_total: int,
    visible_output_tokens: int,
    reasoning_tokens: int,
    legacy_input_tokens: int,
    raw_usage: Any,
) -> dict[str, Any]:
    return {
        "token_accounting_version": TOKEN_ACCOUNTING_VERSION,
        "llm_call_index": call_index,
        "turn": turn,
        "provider": provider,
        "model": model,
        "usage_source": usage_source,
        "usage_status": usage_status,
        "input_tokens_total": input_tokens_total,
        "input_tokens_uncached": input_tokens_uncached,
        "legacy_input_tokens": legacy_input_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "output_tokens_total": output_tokens_total,
        "visible_output_tokens": visible_output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": input_tokens_total + output_tokens_total,
        "mapping_status": "turn_order",
        "mapped_action_turn": turn,
        "raw_usage_redacted": _json_safe(raw_usage),
    }


def _unwrap_usage_payload(value: Any) -> Any:
    if value is None:
        return None
    nested = _get(value, "token_usage") or _get(value, "usage") or _get(value, "usage_metadata")
    return nested or value


def _has_any(obj: Any, *names: str) -> bool:
    return any(_get(obj, name) is not None for name in names)


def _first_int(obj: Any, *names: str) -> Optional[int]:
    for name in names:
        value = _get(obj, name)
        if value is not None:
            return _int(value)
    return None


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    if hasattr(obj, name):
        return getattr(obj, name)
    if hasattr(obj, "model_dump"):
        try:
            data = obj.model_dump()
            if isinstance(data, dict):
                return data.get(name)
        except Exception:
            pass
    return None


def _int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "<redacted>"
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump())
        except Exception:
            pass
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            key_s = str(key)
            if key_s.lower() in {"data", "image_url"} and isinstance(item, str) and len(item) > 120:
                safe[key_s] = "<redacted>"
            else:
                safe[key_s] = _json_safe(item)
        return safe
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "__dict__"):
        try:
            return _json_safe(vars(value))
        except Exception:
            pass
    return str(value)
