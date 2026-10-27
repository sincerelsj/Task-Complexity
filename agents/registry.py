"""Cross-framework registry for agent experiment runs.

Defines the subject_id convention that lets multiple agent frameworks coexist
in the same `logs/` directory with clean separation, while remaining
backward-compatible with human participant data.

Convention
----------
Parts are joined by DOUBLE underscore ``__`` because model IDs themselves
contain hyphens (e.g., ``claude-opus-4-6``) and single-char separators would
make parsing ambiguous.

    Human:  H__{run:03d}                         e.g. "H__001"
    Agent:  {FRAMEWORK}__{model_id}__{run:03d}   e.g. "ANTHCU__claude-opus-4-6__001"

Framework codes are short (<=6 chars) stable identifiers. Model IDs are the
full API-level strings (``claude-opus-4-6``, ``gpt-5``, ``gemini-2.5-pro``,
...) to preserve provenance for later analysis.

Flask's filename sanitizer (app.py) keeps alphanumerics plus ``-_`` so both
human and agent subject_ids round-trip cleanly through the event log path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# --- Framework codes ---------------------------------------------------------
# Keep these short and stable — they appear in subject_ids and folder names.
FRAMEWORK_CODES: dict[str, str] = {
    "ANTHCU": "anthropic_computer_use",        # Anthropic Computer Use (claude-sonnet-4-6, computer_20251124 tool)
    "OAICU":  "openai_computer_use",           # OpenAI Computer Use (gpt-5.4-mini + GA "computer" tool)
    "GEMCU":  "gemini_computer_use",           # Google Gemini Computer Use (gemini-3-flash-preview by default; 2026-05-27 migrated from gemini-2.5-cu-preview)
    "CLDCR":  "claude_in_chrome",              # Anthropic API + Playwright/CDP + custom tool (claude-haiku-4-5)
    "BROUSE": "browser_use",                   # browser-use (Playwright)
    "LCPW":   "langchain_playwright",          # LangChain Playwright toolkit
}

# Reverse lookup for when you have the slug and need the code.
FRAMEWORK_SLUG_TO_CODE: dict[str, str] = {v: k for k, v in FRAMEWORK_CODES.items()}


# --- Known models (informational; not enforced) ------------------------------
# Listed for convenience so the orchestrator can autocomplete / validate.
# The runner accepts any model string — this list is not a hard allowlist.
# 2026-05-27 updated to reflect:
#   - OAICU migration: gpt-5.5 -> gpt-5.4-mini for cost-reduction runs
#   - GEMCU migration: gemini-2.5-cu-preview (2026-10-16 deprecation) → gemini-3-flash-preview
#   - ANTHCU pin: claude-sonnet-4-6; CLDCR/LCPW current pin: claude-haiku-4-5
#   - BROUSE current variants: gpt-5.4-mini and claude-haiku-4-5
# 2026-06-22 updated to reflect:
#   - ANTHCU 3-tier Claude-only epoch: claude-haiku-4-5 / claude-sonnet-4-6 /
#     claude-opus-4-8 (latest June-2026 lineup). Opus 4.8 is the strongest
#     computer-use model and uses the newer computer_20251124 spec, so
#     computer_use_spec_for_model() routes it through the default (non-Haiku)
#     branch with no code change. Haiku 4.5 still needs computer_20250124.
# DO NOT use gemini-3.5-flash — its model page lists computer use as UNSUPPORTED.
KNOWN_MODELS: dict[str, list[str]] = {
    "ANTHCU": [
        "claude-sonnet-5",     # newest Sonnet tier (2026-07; computer_20251124)
        "claude-opus-4-8",     # current Opus tier (2026-06; computer_20251124)
        "claude-sonnet-4-6",   # current Sonnet tier (computer_20251124)
        "claude-haiku-4-5",    # current Haiku tier (computer_20250124, older CU beta)
        # legacy (do not use for new data, baseline only):
        # "claude-opus-4-6",
        # "claude-sonnet-4-5",
    ],
    "OAICU": [
        "gpt-5.4-mini",     # default after 2026-06-02 cost reduction
        "gpt-5.5",
        "gpt-5.5-pro",
        # legacy (until 2026-07-23 shutdown; requires OAICU_TOOL_TYPE=computer_use_preview):
        # "computer-use-preview",
    ],
    "GEMCU": [
        "gemini-3-flash-preview",  # default after 2026-05-27 migration
        "gemini-3-pro-preview",    # Pro toggle via $env:GEMCU_MODEL
        # legacy (until 2026-10-16 deprecation):
        # "gemini-2.5-computer-use-preview-05-2025",
    ],
    "CLDCR": [
        "claude-haiku-4-5",
        "claude-sonnet-4-6",
        "claude-opus-4-6",
    ],
    "BROUSE": [
        "gpt-5.4-mini",
        "claude-haiku-4-5",
        "claude-sonnet-4-6",
        "gpt-5.5",
        "gemini-3-flash-preview",
    ],
    "LCPW": [
        "claude-haiku-4-5",
        "claude-sonnet-4-6",
        "gpt-5.4-mini",
        "gpt-5.5",
        "gemini-3-flash-preview",
    ],
}


SEPARATOR = "__"
HUMAN_PREFIX = "H"


@dataclass(frozen=True)
class ParsedSubjectId:
    """Structured view of a subject_id."""
    participant_type: str        # "human" or "agent"
    framework_code: Optional[str]  # e.g. "ANTHCU", or None for humans
    framework_slug: Optional[str]  # e.g. "anthropic_computer_use", or None
    agent_model: Optional[str]    # full model id, or None for humans
    run: int                      # 1-indexed repetition number
    raw: str                      # the original subject_id

    @property
    def is_agent(self) -> bool:
        return self.participant_type == "agent"


def build_subject_id(
    *,
    framework_code: Optional[str] = None,
    agent_model: Optional[str] = None,
    run: int,
) -> str:
    """Build a subject_id from components.

    If ``framework_code`` is None (or "H"), returns a human id: ``H__{run:03d}``.
    Otherwise returns ``{FRAMEWORK}__{agent_model}__{run:03d}``.
    """
    run_str = f"{run:03d}"
    if framework_code is None or framework_code.upper() == HUMAN_PREFIX:
        return f"{HUMAN_PREFIX}{SEPARATOR}{run_str}"
    code = framework_code.upper()
    if code not in FRAMEWORK_CODES:
        raise ValueError(f"unknown framework_code: {framework_code!r}. known: {list(FRAMEWORK_CODES)}")
    if not agent_model:
        raise ValueError("agent_model required when framework_code is set")
    return f"{code}{SEPARATOR}{agent_model}{SEPARATOR}{run_str}"


def parse_subject_id(subject_id: str) -> ParsedSubjectId:
    """Parse a subject_id into its structured components.

    Tolerant of malformed/legacy ids: falls back to human with run=0 if parsing fails.
    """
    parts = subject_id.split(SEPARATOR)
    if len(parts) == 2 and parts[0] == HUMAN_PREFIX:
        try:
            run = int(parts[1])
        except ValueError:
            run = 0
        return ParsedSubjectId(
            participant_type="human", framework_code=None, framework_slug=None,
            agent_model=None, run=run, raw=subject_id,
        )
    if len(parts) == 3 and parts[0] in FRAMEWORK_CODES:
        code, model, run_str = parts
        try:
            run = int(run_str)
        except ValueError:
            run = 0
        return ParsedSubjectId(
            participant_type="agent",
            framework_code=code,
            framework_slug=FRAMEWORK_CODES[code],
            agent_model=model,
            run=run,
            raw=subject_id,
        )
    # Unknown shape — treat as legacy/human for analysis convenience.
    return ParsedSubjectId(
        participant_type="human", framework_code=None, framework_slug=None,
        agent_model=None, run=0, raw=subject_id,
    )


def trace_folder_rel(
    *,
    framework_code: str,
    agent_model: str,
    subject_id: str,
    condition_id: str,
    started_at_ms: int,
) -> str:
    """Relative trace folder path under ``logs/agent_traces/``.

    Layout:
        agent_traces/{framework_slug}/{agent_model}/{subject}_{cond}_{ts}/

    Returns the relative path as a POSIX string so it works in Linux
    containers and on Windows hosts alike.
    """
    code = framework_code.upper()
    if code not in FRAMEWORK_CODES:
        raise ValueError(f"unknown framework_code: {framework_code!r}")
    slug = FRAMEWORK_CODES[code]
    return f"agent_traces/{slug}/{agent_model}/{subject_id}_{condition_id}_{started_at_ms}"
