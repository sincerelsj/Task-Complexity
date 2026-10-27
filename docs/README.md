# Documentation

- [Web experiment](experiment/README.md): environment setup, task interaction, routes, and logs.
- [Agent implementations](../agents/README.md): Docker builds, API keys, and framework execution.
- [Batch runners](runners/README.md): condition selection and time-pressure sessions.
- [Experimental settings](10_experimental_control_matrix.md): prompts, budgets, display, and logging.
- [Timing](08_time_pressure_throttle.md): rate-limit throttling and decision time.
- [Analysis](../analysis/README.md): input formats, measures, and commands.
- [Cost configuration](../costs/README.md): user-entered prices and token accounting.
- [Repository layout](STRUCTURE.md): source directories.
- [Tools](../tools/README.md): configuration loading and development checks.

## Development Commands

Run from the repository root:

```powershell
python tools/check_release.py
python tools/verify_experimental_controls.py
python tools/verify_token_accounting.py
python tools/smoke_web.py
python tools/verify_session_pricing.py
python -m unittest discover -s analysis/arc_search -p "test_*.py" -v
python -m unittest discover -s costs -p "test_*.py" -v
```

These commands use local or synthetic inputs. Real agent sessions use the selected provider's API and the Docker setup described in the agent guides.
