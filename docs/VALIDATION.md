# Release Validation — 2026-10-27

Validation of the source release prepared for [the public paper repository](https://github.com/sincerelsj/task-complexity-ai-agents). These checks cover local code and packaging; they do not establish end-to-end provider runtime compatibility.

## Checks Performed

- Shared experimental-control verifier: passed in the release package.
- Token normalization and trace-ledger checks: passed with synthetic fixtures.
- Flask smoke: all 12 boards render; valid cell reveal and final submission succeed; invalid POSTs return JSON errors. All written event/summary data use a temporary directory.
- Existing ARC/Search Index tests: 10 passed.
- User-price validation and cache accounting tests: 7 passed with synthetic rates.
- Six session entry points: configured and unconfigured price scenarios exercised with fake loops and provider-free fixtures; 12 executions passed.
- Python compilation: all packaged Python files checked.
- PowerShell syntax and ASCII-only scan: passed.
- Release scanner: credential-pattern, private-file, relative-link, and source-hash checks passed with zero findings. Findings report file/line/rule without matching values.
- Key-loader fixture: placeholder keys are skipped, values are never printed, and executable-looking text is loaded literally rather than evaluated.
- Git ignore rules: `.env`, `secrets/`, research logs, and local price configuration are excluded.
- README images: the hand-drawn conceptual overview and original experiment screenshot are registered in `assets/manifest.json`. The user screenshot's SHA-256 matches its original 2104×1231 PNG; no crop, resize, retouch, or re-encoding was applied. Relative image links and both asset hashes pass the release checker.

## Packaging Repairs

Three source batch files ended with an unterminated `Write-Host` summary string. The release copies of BROUSE, CLDCR, and LCPW all-condition runners now end with a complete summary message. Original development files remain intact.

The verifier's pricing assertion was updated to check normalized tokens and the shared user-price calculator. The scientific control checks remain in place. Source provenance is recorded in `source_manifest.json`.

## Runtime Limits

No Docker image was built, no dependency was installed, and no real provider call was made. Agent-loop code and experimental inputs are preserved, but the optional cost reporter adds a shared module that must be baked into the rebuilt base and framework images. Synthetic entry-point tests do not prove provider/model compatibility or full browser execution.

Numerical paper reproduction requires the analysis inputs that are not included in this source release; see [release scope](README.md). The final PDF's DOI is recorded without a successful online resolution check. A source-code license has not yet been selected.
