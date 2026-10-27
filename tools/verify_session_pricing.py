"""Exercise six session entry points with fake agent loops; no API calls."""
import ast
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
shared = ModuleType('shared')
shared.__path__ = [str(ROOT/'agents/_shared')]
sys.modules['shared'] = shared
from shared.token_accounting import usage_from_anthropic

FRAMEWORKS = dict(computer_use='ANTHCU', openai_computer_use='OAICU',
                  gemini_computer_use='GEMCU', claude_in_chrome='CLDCR',
                  browser_use='BROUSE', langchain_playwright='LCPW')


class FakeTrace:
    def __init__(self, **kwargs):
        self.session_dir = Path('synthetic-trace-not-written')
        self.messages_path = self.session_dir / 'messages.jsonl'
    def record_turn(self, *args, **kwargs):
        pass
    def write_summary(self, **kwargs):
        pass


def main():
    credentials = patch.dict(os.environ, dict(OPENAI_API_KEY='test-key', ANTHROPIC_API_KEY='test-key', GEMINI_API_KEY='test-key', GOOGLE_API_KEY='test-key', GOOGLE_GENAI_USE_VERTEXAI=''))
    credentials.start()
    old_rates = os.environ.pop('EXPERIMENT_PRICING_JSON', None)
    count = 0
    temporary = tempfile.TemporaryDirectory()
    try:
        for folder, code in FRAMEWORKS.items():
            path = ROOT/f'agents/{folder}/runner/run_session.py'
            tree = ast.parse(path.read_text(encoding='utf-8'))
            attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'result'}
            fields = dict.fromkeys(attrs, 0)
            record = usage_from_anthropic(SimpleNamespace(input_tokens=100, output_tokens=10,
                    cache_read_input_tokens=200, cache_creation_input_tokens=50), model='test-model', turn=1, call_index=1)
            fields.update(error=None, final_text='done', stop_reason='end_turn', iterations=1,
                          input_tokens=100, output_tokens=10, cache_read_tokens=200, cache_creation_tokens=50,
                          token_usage_records=[record], thinking_mode='off', thinking_display='omitted', thinking_effort=None)
            fake_loop = ModuleType('runner.loop')
            for node in tree.body:
                if isinstance(node, ast.ImportFrom) and node.module == 'runner.loop':
                    for name in node.names:
                        setattr(fake_loop, name.name, 0)
            fake_loop.run_agent_session = lambda **kwargs: SimpleNamespace(**fields)
            sys.modules['runner'] = ModuleType('runner')
            sys.modules['runner.loop'] = fake_loop
            spec = importlib.util.spec_from_file_location('synthetic_session', path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.TraceLogger = FakeTrace
            arg_names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == 'args'}
            args = dict.fromkeys(arg_names, 0)
            args.update(framework_code=code, subject_id='SYNTHETIC__001', condition_id='C1',
                        agent_model='test-model', url='http://localhost:5000', task='synthetic task',
                        traces_root=temporary.name, thinking_mode='off', time_limit_seconds=None)
            module.parse_args = lambda: SimpleNamespace(**args)
            for configured in [False, True]:
                if configured:
                    os.environ['EXPERIMENT_PRICING_JSON'] = json.dumps({'test-model': dict(input=2, output=7, cache_read=1, cache_write=3)})
                else:
                    os.environ.pop('EXPERIMENT_PRICING_JSON', None)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    exit_code = module.main()
                text = output.getvalue()
                result = json.loads(text[text.index('{'):])
                assert exit_code == 0 and result['ok'], folder
                if configured:
                    assert abs(result['estimated_cost_usd'] - .00062) < 1e-10, folder
                else:
                    assert result['estimated_cost_usd'] is None, folder
                    assert result['cost_estimate_status'] == 'rates_not_configured', folder
                assert all(not isinstance(value, dict) for value in result.values()), folder
                count += 1
    finally:
        if old_rates is None:
            os.environ.pop('EXPERIMENT_PRICING_JSON', None)
        else:
            os.environ['EXPERIMENT_PRICING_JSON'] = old_rates
        credentials.stop()
        temporary.cleanup()
    print(f'Session pricing passed: {count} synthetic executions across six entry points; no API calls.')


if __name__ == '__main__':
    main()
