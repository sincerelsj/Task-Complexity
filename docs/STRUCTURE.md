# Repository Layout

```text
task-complexity-ai-agents/
├── README.md                  # Short paper introduction and component links
├── CITATION.cff               # GitHub citation metadata
├── .env.example               # Placeholder keys only
├── app.py                     # Existing Flask entry point
├── requirements.txt
├── task_prompt.txt            # Exact experimental task
├── data/                      # Three stimulus/condition CSVs + README
├── templates/                 # Experiment pages + README
├── static/                    # Browser tracking/styles + README
├── agents/
│   ├── README.md              # Shared setup, keys, and trace navigation
│   ├── registry.py
│   ├── _base/                 # Shared Docker runtime
│   ├── _shared/               # Prompts, traces, tokens, optional pricing
│   ├── computer_use/          # ANTHCU
│   ├── openai_computer_use/   # OAICU
│   ├── gemini_computer_use/   # GEMCU
│   ├── claude_in_chrome/      # CLDCR
│   ├── browser_use/           # BROUSE
│   └── langchain_playwright/  # LCPW
├── analysis/                  # ARC/SI implementation, tests, action audit
├── costs/                     # User-entered rate template and calculator
├── paper/                     # Paper metadata and BibTeX
├── assets/                    # Overview illustration and original screenshot
├── docs/                      # Web setup, controls, runners, validation
├── tools/                     # Safe key loader and verification tools
└── run_*.ps1                  # Existing batch entry points, made portable
```

The release is a separate local package assembled from reviewed source files.
Original research files remain in the development workspace. There are no moves
or deletions of the working experiment or its data.

Web sources keep their original root paths so Flask template discovery and
Docker host/trace paths continue to work. Each agent keeps its own Compose
context and runner directory. Root PowerShell entry points keep their names for
compatibility; only local paths, optional cost reporting, and broken summary
footers were adjusted.

Development-only authoring files, private logs, credentials, manuscript drafts,
and historical result aggregates are not copied. Empty runtime log directories
and Python caches are generated locally and ignored by Git.
