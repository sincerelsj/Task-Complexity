# Web Experiment

The web task is implemented by the root `app.py`, with [templates](../../templates/README.md), [browser code](../../static/README.md), and [stimulus CSVs](../../data/README.md). These paths are retained to preserve Docker mounts and runner assumptions.

## Setup

Use Python 3.10+ and an isolated environment. To install dependencies yourself:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Run these commands from the repository root. The web server binds to port 5000; containers reach it through `http://host.docker.internal:5000` on Docker Desktop. The experiment does not load or require provider keys.

## Task and Conditions

Alternatives are apartment rows; dimensions are columns. Each cell starts masked, reveals its value after a click, and remains open. Clicking an apartment choice immediately submits the decision. There is no separate final-submit action.

The condition matrix crosses 2/4/8/12 alternatives with 4/8/12 dimensions. Cell values are fetched through `/reveal` and are not embedded in the initial DOM, preventing DOM-based agents from reading unrevealed values. Do not alter the task wording or CSVs to improve appearance.

The row-randomization switch shuffles apartment display order and records its mapping. It is off by default for fixed-order agent sessions.

## HTTP Interface

- `GET /`: participant and condition selection.
- `GET /experiment`: task page, with subject and condition query parameters.
- `POST /reveal`: validate a cell, return its value, and record acquisition.
- `POST /log`: accept session-start, raw-click, and choice-selection events.
- `POST /submit`: validate the selected apartment and record the final decision.
- `GET /complete`: completion page.

Malformed POST requests return JSON errors. Browser display requirements and provider coordinate transforms are documented in the [experimental controls](../10_experimental_control_matrix.md).

## Behavioral Logs

`logs/events_<subject>_<condition>_<timestamp>.csv` records timestamp, subject, condition, event type, apartment, dimension, value, elapsed milliseconds, and coordinates. `logs/summary.csv` records session metadata, participant/framework/model, time, total clicks, unique acquired cells, proportion searched, final choice, and start/end times.

Use a fresh subject ID for every run and rerun. Do not open active CSV logs in Excel: file locks can interrupt submission. Logs are generated locally and excluded from version control.

## Validation

`python tools/smoke_web.py` exercises all 12 boards and reveal/submit/error paths using temporary logs. It performs no paid API calls. External styles referenced by the templates may require network access for full browser styling; Flask tests do not validate visual layout.
