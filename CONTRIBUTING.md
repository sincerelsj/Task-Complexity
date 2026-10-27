# Development and experimental comparability

Keep experimental instructions in `agents/_shared/prompts.py`. Follow
`docs/10_experimental_control_matrix.md` before editing prompts, model settings,
viewport, runner flags, or logging. Do not change the framework/model registry,
stimuli, or condition definitions as a formatting cleanup.

Run from the repository root:

```powershell
python tools\verify_experimental_controls.py
python tools\verify_token_accounting.py
python tools\check_release.py
```

PowerShell files must remain ASCII-only; load the exact task from UTF-8
`task_prompt.txt`. Never print keys, commit `.env`, or include raw logs.

Static checks do not prove provider compatibility. Rebuild the relevant image
and run a new-subject smoke session after modifying Docker-baked code. A normal
provider stop and an error-free `summary.json` are required for success;
exit code zero alone is insufficient. Do not reuse a failed subject ID.
