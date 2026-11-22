"""UAT script generator.

The client runs this script to accept the build. It's generated from the plan, so every
test traces back to the client's own words. Items without an acceptance test are listed
anyway, flagged, so a missing test is visible instead of silently skipped.
"""

from __future__ import annotations

from copilot.planner.schema import Plan
from copilot.projects.requirements import Unit

MISSING_TEST = "(no acceptance test, ask the implementer)"


def render_uat_script(plan: Plan, units: list[Unit], client: str) -> str:
    items = {i.id: i for i in plan.items}
    unit_text = {u.id: u.text for u in units}
    sections: list[str] = []
    for entry in plan.coverage:
        if entry.status != "covered":
            continue
        cited = [items[ref] for ref in entry.refs if ref in items]
        lines = [f"- [ ] {i.id} {i.title}: {i.acceptance_test or MISSING_TEST}" for i in cited]
        if lines:
            heading = f"## {entry.unit_id}: {unit_text.get(entry.unit_id, '')}"
            sections.append("\n".join([heading, "", *lines]))

    title = f"# UAT script: {client} (plan v{plan.version})"
    body = "\n\n".join(sections) if sections else "_Nothing to test yet._"
    return f"{title}\n\n{body}\n"
