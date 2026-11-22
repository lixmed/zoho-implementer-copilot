"""UAT script generator. >>> FOR YOU TO WRITE <<<

The client runs this script to accept the build. It's generated from the plan, so every
test traces back to the client's own words.

Spec
----
render_uat_script(plan: Plan, units: list[Unit], client: str) -> str
    Return Markdown with this structure:

        # UAT script: <client> (plan v<version>)
        <blank line>
        ## <unit.id>: <unit.text>
        <blank line>
        - [ ] <item.id> <item.title>: <item.acceptance_test>
        ...

    Rules:
    1. One "## ..." section per requirement unit whose coverage status is "covered",
       in the same order as plan.coverage.
    2. Inside a section, list the PLAN ITEMS (not questions, not tickets) cited by that
       unit (CoverageEntry.refs that are item ids), in the order they appear in refs.
    3. Items WITHOUT an acceptance_test are listed as:
           - [ ] <item.id> <item.title>: (no acceptance test, ask the implementer)
       so a missing test is visible instead of silently skipped.
    4. Skip a section entirely if none of its refs are plan items (e.g. ticket-only).
    5. If nothing is left at all, return the title line, a blank line, and
       "_Nothing to test yet._"
    6. Formatting: a blank line after the title, after each "## " heading and between
       sections, and the whole string ends with exactly one "\n". test_uat.py shows the
       exact expected text.

Tests: tests/test_uat.py      Run: uv run pytest tests/test_uat.py
Hint:  {i.id: i for i in plan.items} gives you a lookup table.
"""

from __future__ import annotations

from copilot.planner.schema import Plan
from copilot.projects.requirements import Unit


def render_uat_script(plan: Plan, units: list[Unit], client: str) -> str:
    raise NotImplementedError("for you to write, see the module docstring")
