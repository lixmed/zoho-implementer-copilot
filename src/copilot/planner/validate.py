"""Validation, coverage and delivery classification for plan drafts.

Errors block saving. Warnings are saved with the plan and shown in plan.md.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from copilot.planner.schema import (
    ConnectionItem,
    CoverageEntry,
    Delivery,
    FieldItem,
    FunctionItem,
    Issue,
    LayoutItem,
    ModuleItem,
    PicklistChangeItem,
    PlanDraft,
    UiOnlyItem,
    WorkflowItem,
)
from copilot.projects.requirements import Unit


@dataclass
class OrgMetadata:
    """What the validator needs to know about the live org: module -> field api_names."""

    fields_by_module: dict[str, set[str]] = field(default_factory=dict)
    edition: str | None = None
    available: bool = True

    def has_module(self, module: str) -> bool:
        return module in self.fields_by_module

    def has_field(self, module: str, field_api: str) -> bool:
        return field_api in self.fields_by_module.get(module, set())


def delivery_for(item) -> Delivery:
    """Phase 0 coverage table, encoded. See docs/zoho-api-coverage.md section 9."""
    if isinstance(item, (ConnectionItem, UiOnlyItem)):
        return "handoff"
    if isinstance(item, LayoutItem):
        # new layouts have no API; edits need structured placements to be applied by the Builder
        return "api" if (not item.create_new and item.field_placements) else "handoff"
    if isinstance(item, WorkflowItem):
        # needs structured conditions, and only field-update/task actions have verified formats
        # (function attachment, email templates and webhooks stay handoff steps)
        return "api" if item.api_ready() else "handoff"
    return "api"  # field, picklist_change, module, function (code deploy)


def compute_coverage(draft: PlanDraft, units: list[Unit]) -> list[CoverageEntry]:
    by_unit_work: dict[str, list[str]] = defaultdict(list)
    by_unit_questions: dict[str, list[str]] = defaultdict(list)
    for item in draft.items:
        for s in item.sources:
            by_unit_work[s].append(item.id)
    for t in draft.tickets:
        for s in t.sources:
            by_unit_work[s].append(t.id)
    for q in draft.questions:
        for s in q.sources:
            by_unit_questions[s].append(q.id)
    oos = {o.unit_id: o.reason for o in draft.out_of_scope}

    out = []
    for u in units:
        work, questions = by_unit_work.get(u.id, []), by_unit_questions.get(u.id, [])
        if work:
            note = f"open question: {', '.join(questions)}" if questions else None
            out.append(
                CoverageEntry(unit_id=u.id, status="covered", refs=work + questions, note=note)
            )
        elif questions:
            out.append(CoverageEntry(unit_id=u.id, status="needs_clarification", refs=questions))
        elif u.id in oos:
            out.append(CoverageEntry(unit_id=u.id, status="out_of_scope", note=oos[u.id]))
        else:
            out.append(CoverageEntry(unit_id=u.id, status="not_addressed"))
    return out


def _split_ref(ref: str) -> tuple[str, str] | None:
    parts = ref.split(".")
    return (parts[0], parts[1]) if len(parts) == 2 and all(parts) else None


def validate(
    draft: PlanDraft, units: list[Unit], org: OrgMetadata
) -> tuple[list[Issue], dict[str, Delivery], list[CoverageEntry]]:
    issues: list[Issue] = []

    def err(ref: str | None, msg: str) -> None:
        issues.append(Issue(level="error", ref=ref, message=msg))

    def warn(ref: str | None, msg: str) -> None:
        issues.append(Issue(level="warning", ref=ref, message=msg))

    unit_ids = {u.id for u in units}
    all_ids = (
        [i.id for i in draft.items]
        + [q.id for q in draft.questions]
        + [t.id for t in draft.tickets]
    )
    known_ids = set(all_ids)
    for dup in {x for x in all_ids if all_ids.count(x) > 1}:
        err(dup, "duplicate id")

    # --- traceability ---
    cited: Iterable = [*draft.items, *draft.questions, *draft.tickets]
    for obj in cited:
        for s in obj.sources:
            if s not in unit_ids:
                err(obj.id, f"source {s!r} is not a requirement unit id")
    for o in draft.out_of_scope:
        if o.unit_id not in unit_ids:
            err(None, f"out_of_scope cites unknown unit {o.unit_id!r}")

    for item in draft.items:
        for dep in item.depends_on:
            if dep not in known_ids:
                err(item.id, f"depends_on unknown id {dep!r}")
    for q in draft.questions:
        for b in q.blocks:
            if b not in known_ids:
                err(q.id, f"blocks unknown id {b!r}")
    for t in draft.tickets:
        for r in t.related_items:
            if r not in known_ids:
                err(t.id, f"related_items has unknown id {r!r}")

    # --- metadata: what exists in org + what this plan creates ---
    planned_modules = {i.api_name for i in draft.items if isinstance(i, ModuleItem)}
    planned_fields: dict[str, set[str]] = defaultdict(set)
    for i in draft.items:
        if isinstance(i, FieldItem):
            planned_fields[i.module].add(i.api_name)

    def module_ok(module: str) -> bool:
        return module in planned_modules or not org.available or org.has_module(module)

    def field_ok(module: str, field_api: str) -> bool:
        if field_api in planned_fields.get(module, set()) or field_api == "id":
            return True
        if module in planned_modules:
            return False  # a brand-new module only has the fields this plan gives it
        return not org.available or org.has_field(module, field_api)

    def check_refs(owner: str, refs: list[str]) -> None:
        for ref in refs:
            parsed = _split_ref(ref)
            if not parsed:
                err(owner, f"field reference {ref!r} must look like 'Module.Field_Api_Name'")
            elif not module_ok(parsed[0]):
                err(owner, f"module {parsed[0]!r} does not exist in the org or this plan")
            elif not field_ok(*parsed):
                err(owner, f"field {ref!r} does not exist in the org or this plan")

    if not org.available:
        warn(None, "org metadata unavailable: field/module names were NOT checked against the org")

    planned_links = {i.link_name for i in draft.items if isinstance(i, ConnectionItem)}
    for i in draft.items:
        if isinstance(i, FieldItem):
            if not module_ok(i.module):
                err(i.id, f"module {i.module!r} does not exist in the org or this plan")
            elif org.available and org.has_field(i.module, i.api_name):
                err(i.id, f"{i.module}.{i.api_name} already exists in the org; reuse or rename")
            if i.data_type == "lookup" and not i.lookup_module:
                err(i.id, "lookup field needs lookup_module")
            if i.lookup_module and not module_ok(i.lookup_module):
                err(i.id, f"lookup_module {i.lookup_module!r} does not exist")
            if i.data_type in ("picklist", "multiselectpicklist") and not i.picklist_values:
                warn(i.id, "picklist without values")
        elif isinstance(i, PicklistChangeItem):
            check_refs(i.id, [f"{i.module}.{i.field_api_name}"])
        elif isinstance(i, ModuleItem):
            if org.available and org.has_module(i.api_name):
                err(i.id, f"module {i.api_name!r} already exists")
            warn(i.id, "custom modules via API need Enterprise edition or higher (500 credits)")
        elif isinstance(i, LayoutItem):
            if not module_ok(i.module):
                err(i.id, f"module {i.module!r} does not exist in the org or this plan")
            check_refs(i.id, [f"{i.module}.{p.field}" for p in i.field_placements])
            if not i.create_new and not i.field_placements:
                warn(
                    i.id,
                    "no field_placements: stays a handoff step (the Builder can't apply free text)",
                )
        elif isinstance(i, WorkflowItem):
            if not module_ok(i.module):
                err(i.id, f"module {i.module!r} does not exist in the org or this plan")
            check_refs(i.id, i.criteria_fields)
            check_refs(i.id, [f"{i.module}.{c.field}" for c in i.conditions])
            for a in i.actions:
                check_refs(i.id, a.uses_fields)
                if a.field_update:
                    check_refs(i.id, [f"{i.module}.{a.field_update.field}"])
            if any(a.type == "email_notification" for a in i.actions):
                warn(i.id, "email notification needs an existing email template (no create API)")
            fn_actions = [a for a in i.actions if a.type == "function" and a.function]
            planned_fns = {f.name: f for f in draft.items if isinstance(f, FunctionItem)}
            for a in fn_actions:
                f = planned_fns.get(a.function)
                if f is not None and f.category != "automation":
                    err(
                        i.id,
                        f"function {a.function!r} is {f.category}; workflows can only call automation functions",
                    )
            if fn_actions:
                warn(i.id, "EXPERIMENTAL function attachment: the rule will be created INACTIVE; "
                     "check the function's argument mapping in Zoho before activating it")  # fmt: skip
            if not i.api_ready():
                reasons = []
                if not i.conditions:
                    reasons.append("no structured conditions")
                if i.trigger not in ("create", "edit", "create_or_edit", "delete"):
                    reasons.append(f"trigger {i.trigger!r} not supported by the Builder")
                reasons += [f"{a.type} action" for a in i.actions if not a.api_ready()]
                warn(i.id, "stays a handoff step: " + ", ".join(reasons))
        elif isinstance(i, FunctionItem):
            check_refs(i.id, i.uses_fields)
            for link in i.connections:
                if link not in planned_links:
                    warn(
                        i.id,
                        f"connection {link!r} not in this plan; it must already exist in "
                        "the org (cannot be checked via API)",
                    )
        elif isinstance(i, ConnectionItem):
            for s in i.scopes:
                if not s.reason.strip():
                    err(i.id, f"scope {s.scope} has no reason")
            if any(s.scope.endswith(".ALL") for s in i.scopes):
                warn(i.id, "uses an .ALL scope; is a narrower scope enough?")

    # --- delivery and coverage ---
    delivery = {i.id: delivery_for(i) for i in draft.items}
    for q in draft.questions:
        for b in q.blocks:
            if b in delivery:
                delivery[b] = "decision"

    coverage = compute_coverage(draft, units)
    missing = [c.unit_id for c in coverage if c.status == "not_addressed"]
    if missing:
        shown = ", ".join(missing[:15]) + (" ..." if len(missing) > 15 else "")
        err(
            None,
            f"{len(missing)} requirement unit(s) not addressed: {shown}. Cite them from an "
            "item, question or ticket, or list them in out_of_scope with a reason.",
        )
    return issues, delivery, coverage
