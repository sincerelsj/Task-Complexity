"""Shared system prompt components for all agent frameworks.

Usage
-----
For pixel-based CU frameworks (ANTHCU, OAICU, GEMCU):

    from agents._shared.prompts import SYSTEM_PROMPT

For frameworks with custom toolsets (CLDCR, LCPW), append a Tools section:

    from agents._shared.prompts import SYSTEM_PROMPT, TOOL_SECTION_HEADER
    SYSTEM_PROMPT = SYSTEM_PROMPT + TOOL_SECTION_HEADER + "<tool-specific text>\n"
"""

PROMPT_VERSION = "2026-06-10.choice-submit-v2"

SYSTEM_PROMPT = """\
You are operating a web browser to complete a task.

Rules:
- Read information on the page by clicking the cells you think are necessary
  Each cell stays open once clicked.
- Use at most one tool call or UI action in each response. Do not batch clicks
  or tool calls; wait for the next screenshot/observation before acting again.
- There is no time limit.
- When you are ready, click one apartment's choice button. That single click
  submits the task; there is no separate submit button.
- After you click a choice button, stop acting and report completion. Do not
  click that choice again or look for another submit button.
- Stay on the experiment page. Do not navigate away, open new tabs, search the
  web, or change the URL unless the page has failed to load.
- If a page fails to load, take a screenshot and report back; do not retry
  navigation yourself.
"""

# --- Time-pressure variant ---------------------------------------------------
# Used by experimental sessions that impose a soft wall-clock deadline. The
# loop formats this template fresh before EVERY API call so the model sees the
# remaining time tick down. When remaining_seconds <= 0 the loop exits without
# making another call (so the model does not get to act past the deadline).
#
# Required placeholders: {time_limit_seconds}, {remaining_seconds}.
TIME_PRESSURE_SYSTEM_PROMPT_TEMPLATE = """\
You are operating a web browser to complete a task UNDER TIME PRESSURE.

Rules:
- Read information on the page by clicking the cells you think are necessary.
  Each cell stays open once clicked.
- Use at most one tool call or UI action in each response. Do not batch clicks
  or tool calls; wait for the next screenshot/observation before acting again.
- TIME LIMIT: {time_limit_seconds} seconds total for this task.
- TIME REMAINING: {remaining_seconds} seconds.
  If you do not submit before time runs out, the trial ends with no choice
  recorded. Manage your remaining time accordingly.
- When you are ready, click one apartment's choice button. That single click
  submits the task; there is no separate submit button.
- After you click a choice button, stop acting and report completion. Do not
  click that choice again or look for another submit button.
- Stay on the experiment page. Do not navigate away, open new tabs, search the
  web, or change the URL unless the page has failed to load.
- If a page fails to load, take a screenshot and report back; do not retry
  navigation yourself.
"""

# Append this before framework-specific tool descriptions.
TOOL_SECTION_HEADER = "\nTools:\n"
