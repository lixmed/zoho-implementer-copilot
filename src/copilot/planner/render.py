"""Markdown renderers: plan.md (for the implementer), sign-off doc (for the client),
change request (for both)."""

from __future__ import annotations

from collections import Counter

from copilot.planner.schema import (
    ConnectionItem,
    FieldItem,
    FunctionItem,
    Plan,
    WorkflowItem,
)
from copilot.planner.store import PlanDiff
from copilot.projects.requirements import Unit

DELIVERY_TITLES = {
    "api": "Applied via API (after your approval, sandbox first)",
    "handoff": "Handoff: placed by hand from the handoff pack",
    "decision": "Waiting on a client decision",
}
STATUS_ICON = {
    "covered": "✅",
    "needs_clarification": "❓",
    "out_of_scope": "➖",
    "not_addressed": "❌",
}


def _cell(text: str, limit: int = 110) -> str:
    text = text.replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _hours(minutes: int) -> str:
    return f"{minutes / 60:.1f} h"


def _item_detail(item) -> str:
    if isinstance(item, FieldItem):
        extra = f", values: {', '.join(item.picklist_values)}" if item.picklist_values else ""
        lookup = f" → {item.lookup_module}" if item.lookup_module else ""
        return f"`{item.module}.{item.api_name}` ({item.data_type}{lookup}{extra})"
    if isinstance(item, WorkflowItem):
        acts = ", ".join(a.type for a in item.actions)
        return f"`{item.module}` on {item.trigger} when {item.criteria} → {acts}"
    if isinstance(item, FunctionItem):
        return f"`{item.name}` ({item.category}): {item.purpose}"
    if isinstance(item, ConnectionItem):
        return f"{item.service}, link name `{item.link_name}`"
    return item.kind


def render_plan_md(plan: Plan, units: list[Unit]) -> str:
    unit_text = {u.id: u.text for u in units}
    counts = Counter(c.status for c in plan.coverage)
    total_min = sum(i.estimate_minutes for i in plan.items)
    ticket_h = sum(t.estimate_hours for t in plan.tickets)

    out = [
        f"# Implementation plan: {plan.project} (v{plan.version})",
        "",
        f"_Org: {plan.org or 'not linked'} · created {plan.created_at:%Y-%m-%d %H:%M} UTC"
        + (f" · revises v{plan.based_on_version}_" if plan.based_on_version else "_"),
        "",
        plan.summary,
        "",
        f"**Coverage:** {counts['covered']} covered · {counts['needs_clarification']} need "
        f"clarification · {counts['out_of_scope']} out of scope · "
        f"{counts['not_addressed']} not addressed",
        f"**Estimate:** {_hours(total_min)} configuration + {ticket_h:.1f} h developer tickets",
        "",
    ]
    for level in ("error", "warning"):
        found = [i for i in plan.issues if i.level == level]
        if found:
            out.append(f"### {level.title()}s")
            out += [f"- {f'**{i.ref}** ' if i.ref else ''}{i.message}" for i in found]
            out.append("")

    for delivery, title in DELIVERY_TITLES.items():
        items = [i for i in plan.items if plan.delivery.get(i.id) == delivery]
        if not items:
            continue
        out += [
            f"## {title}",
            "",
            "| ID | What | Detail | Est. | Sources |",
            "|---|---|---|---|---|",
        ]
        for i in items:
            out.append(
                f"| {i.id} | {_cell(i.title, 60)} | {_cell(_item_detail(i))} | "
                f"{i.estimate_minutes}m | {', '.join(i.sources)} |"
            )
        out.append("")

    conns = [i for i in plan.items if isinstance(i, ConnectionItem)]
    if conns:
        out += ["## Connections and scopes", ""]
        for c in conns:
            out.append(f"**{c.connection_name}** ({c.service}), link name `{c.link_name}`")
            out += [f"- `{s.scope}`: {s.reason}" for s in c.scopes]
            out.append("")

    if plan.questions:
        out += ["## Questions for the client", ""]
        for q in plan.questions:
            blocks = f" _(blocks {', '.join(q.blocks)})_" if q.blocks else ""
            out.append(f"**{q.id}.** {q.question}{blocks}  ")
            out.append(f"Why: {q.why_it_matters}")
            if q.options:
                out.append(f"Options: {' / '.join(q.options)}")
            out.append("")

    if plan.tickets:
        out += ["## Developer tickets", ""]
        for t in plan.tickets:
            out += [f"### {t.id}: {t.title} ({t.estimate_hours:g} h)", "", t.description, ""]
            out += [f"- [ ] {c}" for c in t.acceptance_criteria]
            out.append("")

    out += [
        "## Requirements coverage",
        "",
        "| | Unit | Requirement | Covered by |",
        "|---|---|---|---|",
    ]
    for c in plan.coverage:
        refs = ", ".join(c.refs) or (c.note or "")
        out.append(
            f"| {STATUS_ICON[c.status]} | {c.unit_id} | {_cell(unit_text.get(c.unit_id, '?'))} | "
            f"{_cell(refs, 60)} |"
        )
    out.append("")
    return "\n".join(out)


def render_signoff(plan: Plan, units: list[Unit], client: str) -> str:
    """Plain-language document the client signs: what we'll build, per requirement."""
    items = {i.id: i for i in plan.items}
    questions = {q.id: q for q in plan.questions}
    tickets = {t.id: t for t in plan.tickets}
    unit_by_id = {u.id: u for u in units}

    out = [
        f"# {client}: CRM implementation scope (plan v{plan.version})",
        "",
        plan.summary,
        "",
        "## What we will deliver, requirement by requirement",
        "",
    ]
    for c in plan.coverage:
        u = unit_by_id.get(c.unit_id)
        if not u or c.status == "out_of_scope":
            continue
        out.append(f"> {u.text}")
        out.append("")
        for ref in c.refs:
            if ref in items:
                out.append(f"- {items[ref].title}: {items[ref].rationale}")
            elif ref in tickets:
                out.append(f"- {tickets[ref].title} (custom development)")
            elif ref in questions:
                out.append(f"- ❓ To confirm: {questions[ref].question}")
        out.append("")

    oos = [c for c in plan.coverage if c.status == "out_of_scope"]
    if oos:
        out += ["## Not included", ""]
        out += [
            f'- "{_cell(unit_by_id[c.unit_id].text, 140)}": {c.note}'
            for c in oos
            if c.unit_id in unit_by_id
        ]
        out.append("")
    if plan.questions:
        out += ["## Open questions (answers needed before we build the affected parts)", ""]
        out += [f"{n}. {q.question}" for n, q in enumerate(plan.questions, 1)]
        out.append("")
    out += [
        "## Sign-off",
        "",
        "Approved by (client): ______________________   Date: ____________",
        "",
        "Approved by (implementer): _________________   Date: ____________",
        "",
    ]
    return "\n".join(out)


def render_change_request(diff: PlanDiff, old: Plan, new: Plan) -> str:
    out = [
        f"# Change request: plan v{diff.old_version} → v{diff.new_version}",
        "",
        f"**Effort change:** {diff.estimate_delta_minutes:+d} min configuration, "
        f"{diff.estimate_delta_ticket_hours:+.1f} h developer tickets",
        "",
    ]
    if diff.added:
        out += ["## Added", "", "| ID | Kind | What | Est. |", "|---|---|---|---|"]
        out += [
            f"| {a['id']} | {a['kind']} | {_cell(a['title'], 70)} | +{a['estimate_minutes']}m |"
            for a in diff.added
        ]
        out.append("")
    if diff.removed:
        out += ["## Removed", "", "| ID | Kind | What | Est. |", "|---|---|---|---|"]
        out += [
            f"| {r['id']} | {r['kind']} | {_cell(r['title'], 70)} | -{r['estimate_minutes']}m |"
            for r in diff.removed
        ]
        out.append("")
    if diff.changed:
        out += ["## Changed", ""]
        for ch in diff.changed:
            out.append(f"**{ch.id}: {ch.title}** ({ch.estimate_delta_minutes:+d}m)")
            for name, (a, b) in ch.fields.items():
                out.append(f"- `{name}`: {_cell(str(a), 80)} → {_cell(str(b), 80)}")
            out.append("")
    if diff.questions_added or diff.questions_resolved:
        out += ["## Questions", ""]
        if diff.questions_added:
            out.append(f"- New: {', '.join(diff.questions_added)}")
        if diff.questions_resolved:
            out.append(f"- Resolved: {', '.join(diff.questions_resolved)}")
        out.append("")
    if diff.tickets_added or diff.tickets_removed:
        out += ["## Developer tickets", ""]
        if diff.tickets_added:
            out.append(f"- Added: {', '.join(diff.tickets_added)}")
        if diff.tickets_removed:
            out.append(f"- Removed: {', '.join(diff.tickets_removed)}")
        out.append("")
    if not (diff.added or diff.removed or diff.changed):
        out += ["No changes to what gets built.", ""]
    return "\n".join(out)
