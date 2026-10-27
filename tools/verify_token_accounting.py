from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
TOKEN_ACCOUNTING = ROOT / "agents" / "_shared" / "token_accounting.py"


def load_module():
    spec = importlib.util.spec_from_file_location("token_accounting", TOKEN_ACCOUNTING)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load token_accounting.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require(errors: list[str], label: str, ok: bool) -> None:
    if not ok:
        errors.append(label)


def main() -> int:
    t = load_module()
    errors: list[str] = []
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    anthropic = SimpleNamespace(
        input_tokens=100,
        output_tokens=20,
        cache_read_input_tokens=30,
        cache_creation_input_tokens=10,
    )
    a = t.usage_from_anthropic(anthropic, model="claude-haiku-4-5", turn=1, call_index=1)
    require(errors, "anthropic total input includes cache buckets", a["input_tokens_total"] == 140)
    require(errors, "anthropic uncached input is base input", a["input_tokens_uncached"] == 100)
    require(errors, "anthropic legacy input preserves prior field meaning", a["legacy_input_tokens"] == 100)

    openai = {
        "input_tokens": 200,
        "output_tokens": 50,
        "input_tokens_details": {"cached_tokens": 60},
        "output_tokens_details": {"reasoning_tokens": 15},
    }
    o = t.usage_from_openai(openai, model="gpt-5.4-mini", turn=1, call_index=1)
    require(errors, "openai uncached input subtracts cached tokens", o["input_tokens_uncached"] == 140)
    require(errors, "openai visible output subtracts reasoning", o["visible_output_tokens"] == 35)
    require(errors, "openai total tokens uses provider total input/output", o["total_tokens"] == 250)

    gemini = SimpleNamespace(
        prompt_token_count=300,
        candidates_token_count=40,
        cached_content_token_count=90,
        thoughts_token_count=12,
    )
    g = t.usage_from_gemini(gemini, model="gemini-3-flash-preview", turn=1, call_index=1)
    require(errors, "gemini uncached input subtracts cached content", g["input_tokens_uncached"] == 210)
    require(errors, "gemini reasoning/thought tokens captured", g["reasoning_tokens"] == 12)

    lc = {
        "input_tokens": 500,
        "output_tokens": 70,
        "input_token_details": {"cache_read": 100},
        "output_token_details": {"reasoning": 10},
    }
    l = t.usage_from_langchain_payload(
        lc,
        model="claude-haiku-4-5",
        turn=1,
        call_index=1,
        usage_source="fixture",
    )
    require(errors, "langchain fixture normalizes", l is not None)
    if l is not None:
        require(errors, "langchain status exact callback", l["usage_status"] == "exact_langchain_callback")
        require(errors, "langchain cache subtract", l["input_tokens_uncached"] == 400)
        require(errors, "langchain visible output", l["visible_output_tokens"] == 60)

    agg = t.aggregate_token_usage([a, o, g])
    require(errors, "aggregate mixed exact status", agg["token_usage_status"] == "exact_provider")
    require(errors, "aggregate call count", agg["token_usage_calls"] == 3)
    require(errors, "aggregate total tokens", agg["total_tokens"] == a["total_tokens"] + o["total_tokens"] + g["total_tokens"])

    missing = t.aggregate_token_usage([])
    require(errors, "missing usage explicit unavailable", missing["token_usage_status"] == "unavailable")

    try:
        from agents._shared.prompts import PROMPT_VERSION
        from agents._shared.trace_logger import TraceLogger

        with tempfile.TemporaryDirectory() as tmp:
            tracer = TraceLogger(
                root=Path(tmp),
                framework_slug="test_framework",
                agent_model="test-model",
                subject_id="test_subject",
                condition_id="C1",
                started_at_ms=1,
            )
            summary = tracer.write_summary(
                iterations=0,
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                stop_reason="failed",
                wall_seconds=0.0,
                final_text="",
                error="fixture",
                task="fixture task",
                url="http://example.test",
                token_usage_records=[],
            )
            rows = [
                json.loads(line)
                for line in tracer.token_usage_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            require(errors, "trace summary records prompt version", summary.get("prompt_version") == PROMPT_VERSION)
            require(errors, "empty usage still writes token_usage.jsonl", len(rows) == 1)
            if rows:
                require(errors, "fallback usage row has status", rows[0].get("token_usage_status") == "unavailable")
                require(errors, "fallback usage row has prompt version", rows[0].get("prompt_version") == PROMPT_VERSION)
                require(errors, "fallback usage row is marked unavailable", rows[0].get("kind") == "token_usage_unavailable")
    except Exception as exc:
        errors.append(f"trace logger fallback fixture failed: {type(exc).__name__}: {exc}")

    if errors:
        print("Token-accounting verification FAILED:")
        for err in errors:
            print(f" - {err}")
        return 1
    print("Token-accounting verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
