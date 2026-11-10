# Repository Layout

```text
Task-Complexity/
├── README.md                 # Project and paper overview
├── CITATION.cff              # Citation metadata
├── assets/                   # README illustration
└── experiment/               # Working directory for commands
    ├── .env.example              # Provider key placeholders
    ├── app.py                    # Flask server
    ├── requirements.txt
    ├── task_prompt.txt           # Experimental task text
    ├── data/                     # Stimulus and condition CSVs
    ├── templates/                # Task pages
    ├── static/                   # Browser tracking and styles
    ├── agents/
    │   ├── registry.py           # Framework/model identifiers
    │   ├── _base/                # Shared Docker runtime
    │   ├── _shared/              # Prompts, traces, tokens, pricing
    │   ├── computer_use/         # ANTHCU
    │   ├── openai_computer_use/  # OAICU
    │   ├── gemini_computer_use/  # GEMCU
    │   ├── claude_in_chrome/     # CLDCR
    │   ├── browser_use/          # BROUSE
    │   └── langchain_playwright/ # LCPW
    ├── analysis/                 # Search measures and action classification
    ├── costs/                    # User-entered model prices
    ├── paper/                    # Paper information and BibTeX
    ├── docs/                     # Setup and settings guides
    ├── tools/                    # Configuration and checks
    └── run_anthcu_all_conditions.ps1 # Example batch runner
```

Each component contains a README with its input formats, settings, and commands. Runtime event logs and agent traces are written to the locally generated `experiment/logs/` directory.
