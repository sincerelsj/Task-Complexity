# Maintenance Tools

- `load_api_keys.ps1`: read the root `.env` into the current PowerShell process without printing values or executing file contents.
- `check_release.py`: inspect source files for known credential patterns, forbidden release artifacts, broken relative Markdown/image links, non-ASCII PowerShell, and source-manifest drift. Reviewed PNG assets must match their explicit manifest hashes. Credential findings report file and line only.
- `verify_experimental_controls.py`: check shared experimental settings across six frameworks.
- `verify_token_accounting.py`: exercise provider normalization and trace usage records with synthetic inputs.
- `smoke_web.py`: exercise all 12 web boards and JSON reveal/submit/error paths with temporary log output.
- `verify_session_pricing.py`: exercise configured/missing user prices across six CLI entry points with fake loops and no provider calls.

Run Python tools from the repository root. No provider keys or API calls are required. Pattern scanning reduces release risk but does not certify that every possible secret format is detectable.
