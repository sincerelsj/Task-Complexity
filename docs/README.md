# Reproducibility and Release Scope

## Reading Guide

- [Web experiment](experiment/README.md): installation, behavior, routes, and logs.
- [Agents](../agents/README.md): credential setup and framework-specific execution.
- [Runners](runners/README.md): batch scope and portable PowerShell paths.
- [Experimental control matrix](10_experimental_control_matrix.md): shared settings and unavoidable modality differences.
- [Time pressure](08_time_pressure_throttle.md): throttle-adjusted decision time.
- [Costs](../costs/README.md): user-entered prices and normalized usage.
- [Analysis](../analysis/README.md): metrics and available code.
- [Validation report](VALIDATION.md): checks actually performed for this package.
- [Repository layout](STRUCTURE.md): component map and packaging decisions.

## Source Packaging

The release was assembled from an explicit source-file allowlist. Web behavior, agent loops, prompts, stimuli, and UI assets retain their implementation. The six session entry points only change optional cost reporting, and Compose forwards the optional price configuration. Optional cost reporting uses user-entered prices rather than built-in rates. Root PowerShell runners replace the original absolute workspace prefix with `$PSScriptRoot`; credential examples use placeholders. The static verifier reads `CONTRIBUTING.md` instead of a machine-specific agent instruction file. Hashes of included source files and packaging-edit flags are recorded in `source_manifest.json`.

The original development workspace, raw logs, credentials, authoring tools, poster iterations, and intermediate manuscripts remain outside this package. The source-only scope does not change original data retention.

## Scientific Scope

The paper's model study is narrower than the six-framework software implementation. This source release does not pool framework results or distribute historical aggregates as final-paper results.

Raw study data and figure-generation inputs are not included. A fresh clone can run the web task and code checks, but cannot independently regenerate the paper's numerical figures without the corresponding analysis inputs. The bundled runners implement a 20,000-output-token budget; exact execution settings are documented in the control matrix and source code.

The copied control matrix and timing policy contain dated development notes and historical model defaults. Current bundled source is authoritative; future provider changes must not silently replace the original experiment configuration.

## Verification

```powershell
python tools/check_release.py
python tools/verify_experimental_controls.py
python tools/verify_token_accounting.py
python tools/smoke_web.py
python tools/verify_session_pricing.py
python -m unittest discover -s analysis/arc_search -p "test_*.py" -v
python -m unittest discover -s costs -p "test_*.py" -v
```

No paid provider calls are needed for these checks. Fresh Docker builds and new-subject provider smokes are separate runtime checks. No new dependencies, images, or models were installed during packaging. This repository is public; a source-code license has not yet been selected.
