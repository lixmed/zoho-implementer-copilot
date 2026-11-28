"""Approved plan + live org  ->  change set (ordered operations with request bodies and undo info).

Nothing here talks to Zoho except metadata reads through LiveOrg. Values that only exist
after an earlier operation runs (e.g. the id of a field created in this change set) are
written as placeholders "$op:<op_id>" and resolved at apply time.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from copilot.changes.live import LiveOrg
from copilot.planner.schema import (
    FieldItem,
    LayoutItem,
    ModuleItem,
    PicklistChangeItem,
    Plan,
    WorkflowItem,
)

OpKind = Literal[
    "create_module", "create_field", "picklist_add", "picklist_rename", "picklist_retire",
    "place_field", "create_field_update", "create_task", "create_workflow",
]  # fmt: skip
OpStatus = Literal[
    "pending", "applied", "failed", "not_run", "rolled_back", "rollback_failed", "manual"
]

CREDITS = {"create_module": 500, "create_field": 10, "picklist_add": 10, "picklist_rename": 10}
SCOPE_FOR = {
    "create_module": "ZohoCRM.settings.modules.CREATE",
    "create_field": "ZohoCRM.settings.fields.CREATE",
    "picklist_add": "ZohoCRM.settings.fields.UPDATE",
    "picklist_rename": "ZohoCRM.settings.fields.UPDATE",
    "picklist_retire": "ZohoCRM.settings.layouts.UPDATE",
    "place_field": "ZohoCRM.settings.layouts.UPDATE",
    "create_field_update": "ZohoCRM.settings.automation_actions.CREATE",
    "create_task": "ZohoCRM.settings.automation_actions.CREATE",
    "create_workflow": "ZohoCRM.settings.workflow_rules.CREATE",
}


class Operation(BaseModel):
    op_id: str
    item_id: str
    kind: OpKind
    module: str
    summary: str
    request: dict = Field(description="arguments for the write client method")
    undo: dict = Field(default_factory=dict)
    credits: int = 1
    status: OpStatus = "pending"
    created_id: str | None = None
    error: str | None = None


class Skipped(BaseModel):
    item_id: str
    reason: str


class ChangeSet(BaseModel):
    id: str
    project: str
    org: str
    environment: str
    plan_version: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: Literal["preview", "applied", "partial", "rolled_back", "rolled_back_partial"] = (
        "preview"
    )
    operations: list[Operation] = Field(default_factory=list)
    skipped: list[Skipped] = Field(default_factory=list)
    handoff: list[Skipped] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def credits(self) -> int:
        return sum(o.credits for o in self.operations)

    def scopes_needed(self) -> set[str]:
        return {SCOPE_FOR[o.kind] for o in self.operations}


def _ref(op_id: str) -> str:
    return f"$op:{op_id}"


def _rule_name(text: str) -> str:
    # Zoho: max 100 chars, no # % ^ & * ( )
    return re.sub(r"[#%^&*()]", "", text).strip()[:100]


class _Builder:
    def __init__(self, plan: Plan, live: LiveOrg, cs: ChangeSet):
        self.plan, self.live, self.cs = plan, live, cs
        self.n = 0
        self.field_ops: dict[tuple[str, str], str] = {}  # (module, api_name) -> op_id

    def add(
        self,
        item_id: str,
        kind: OpKind,
        module: str,
        summary: str,
        request: dict,
        undo: dict | None = None,
    ) -> str:
        self.n += 1
        op_id = f"op-{self.n:02d}"
        self.cs.operations.append(
            Operation(
                op_id=op_id,
                item_id=item_id,
                kind=kind,
                module=module,
                summary=summary,
                request=request,
                undo=undo or {},
                credits=CREDITS.get(kind, 1),
            )  # fmt: skip
        )
        return op_id

    def skip(self, item_id: str, reason: str) -> None:
        self.cs.skipped.append(Skipped(item_id=item_id, reason=reason))

    def error(self, item_id: str, msg: str) -> None:
        self.cs.errors.append(f"{item_id}: {msg}")

    def field_id(self, module: str, api_name: str) -> str | None:
        """Existing field id, or a placeholder for a field created earlier in this change set."""
        if (module, api_name) in self.field_ops:
            return _ref(self.field_ops[(module, api_name)])
        f = self.live.field(module, api_name)
        return str(f["id"]) if f else None

    # ---------------------------------------------------------------- item handlers

    def module(self, it: ModuleItem) -> None:
        if it.api_name in self.live.modules():
            return self.skip(it.id, f"module {it.api_name} already exists")
        ids, missing = self.live.profile_ids(it.profiles)
        if missing:
            return self.error(it.id, f"unknown profiles {missing}")
        body = {"plural_label": it.plural_label, "singular_label": it.singular_label,
                "api_name": it.api_name, "profiles": [{"id": i} for i in ids]}  # fmt: skip
        self.add(
            it.id,
            "create_module",
            it.api_name,
            f"create module {it.plural_label} (Enterprise+, 500 credits)",
            {"module": body},
        )

    def field(self, it: FieldItem) -> None:
        if self.live.field(it.module, it.api_name):
            return self.skip(it.id, f"{it.module}.{it.api_name} already exists")
        body: dict = {"field_label": it.field_label, "data_type": it.data_type}
        if it.length:
            body["length"] = it.length
        if it.picklist_values:
            body["pick_list_values"] = [
                {"display_value": v, "actual_value": v} for v in it.picklist_values
            ]
        if it.lookup_module:
            body["lookup"] = {
                "module": {"api_name": it.lookup_module},
                "display_label": it.field_label,
            }
        layout_ids = []
        for p in it.placement:
            lay = self.live.layout_by_name(it.module, p.layout)
            if lay:
                layout_ids.append({"id": str(lay["id"])})
            elif it.module in self.live.modules():
                self.error(it.id, f"layout {p.layout!r} not found in {it.module}")
        if layout_ids:
            body["layout_associations"] = layout_ids
        op = self.add(it.id, "create_field", it.module, f"create field {it.module}.{it.api_name} ({it.data_type})",
                      {"module": it.module, "fields": [body], "api_name": it.api_name})  # fmt: skip
        self.field_ops[(it.module, it.api_name)] = op
        for p in it.placement:
            self.place(it.id, it.module, p.layout, p.section, it.api_name, p.required)

    def place(
        self,
        item_id: str,
        module: str,
        layout_name: str,
        section_label: str,
        field: str,
        required: bool,
    ) -> None:
        lay = self.live.layout_by_name(module, layout_name)
        if not lay:
            return self.error(item_id, f"layout {layout_name!r} not found in {module}")
        sec = self.live.section_by_label(lay, section_label)
        if not sec:
            return self.error(
                item_id, f"section {section_label!r} not found in {module}/{layout_name}"
            )
        fid = self.field_id(module, field)
        if not fid:
            return self.error(item_id, f"field {module}.{field} not found")
        undo: dict = {"layout_id": str(lay["id"])}
        if not fid.startswith("$op:"):
            where = self.live.find_field_in_layout(lay, fid)
            if where:
                old_sec, old_f = where
                undo |= {
                    "was_in_layout": True,
                    "section_id": str(old_sec["id"]),
                    "required": bool(old_f.get("required")),
                }
                if str(old_sec["id"]) == str(sec["id"]) and bool(old_f.get("required")) == required:
                    return self.skip(
                        item_id,
                        f"{module}.{field} already in {section_label} (required={required})",
                    )
        layout = {
            "id": str(lay["id"]),
            "sections": [{"id": str(sec["id"]), "fields": [{"id": fid, "required": required}]}],
        }
        self.add(item_id, "place_field", module, f"place {module}.{field} in {layout_name}/{section_label}" + (" (required)" if required else ""),
                 {"module": module, "layout_id": str(lay["id"]), "layout": layout}, undo)  # fmt: skip

    def picklist(self, it: PicklistChangeItem) -> None:
        f = self.live.field(it.module, it.field_api_name)
        if not f:
            return self.error(it.id, f"{it.module}.{it.field_api_name} not found")
        values = self.live.picklist_values(it.module, str(f["id"]))
        before = [
            {
                "display_value": v.get("display_value"),
                "actual_value": v.get("actual_value"),
                "id": str(v.get("id")),
            }
            for v in values
        ]
        existing = {v["display_value"] for v in before} | {v["actual_value"] for v in before}
        layouts = [str(lay["id"]) for lay in self.live.layouts_containing(it.module, str(f["id"]))]
        undo = {"field_id": str(f["id"]), "before_values": before, "layout_ids": layouts}
        new = [v for v in it.add_values if v not in existing]
        if it.add_values and not new:
            self.skip(it.id, f"values {it.add_values} already exist")
        if new:
            self.add(it.id, "picklist_add", it.module, f"add {new} to {it.module}.{it.field_api_name}",
                     {"module": it.module, "field_id": str(f["id"]),
                      "changes": {"pick_list_values": [{"display_value": v, "actual_value": v} for v in new]}}, undo)  # fmt: skip
        renames = []
        for old, newname in it.rename_values.items():
            match = next(
                (v for v in before if old in (v["display_value"], v["actual_value"])), None
            )
            if not match:
                self.error(it.id, f"picklist value {old!r} not found")
            else:
                renames.append(
                    {
                        "display_value": newname,
                        "actual_value": match["actual_value"],
                        "id": match["id"],
                    }
                )
        if renames:
            self.add(it.id, "picklist_rename", it.module, f"rename {list(it.rename_values.items())} in {it.module}.{it.field_api_name}",
                     {"module": it.module, "field_id": str(f["id"]), "changes": {"pick_list_values": renames}}, undo)  # fmt: skip
        if it.retire_values:
            keep = [
                v
                for v in before
                if v["display_value"] not in it.retire_values
                and v["actual_value"] not in it.retire_values
            ]
            for lid in layouts:
                lay = next(x for x in self.live.layouts(it.module) if str(x["id"]) == lid)
                sec, _ = self.live.find_field_in_layout(lay, str(f["id"]))
                layout = {"id": lid, "sections": [{"id": str(sec["id"]), "fields": [{"id": str(f["id"]),
                          "pick_list_values": [{"display_value": v["display_value"], "id": v["id"]} for v in keep]}]}]}  # fmt: skip
                self.add(it.id, "picklist_retire", it.module, f"mark {it.retire_values} unused in {it.module}.{it.field_api_name} (layout {lay.get('name')})",
                         {"module": it.module, "layout_id": lid, "layout": layout}, undo | {"section_id": str(sec["id"])})  # fmt: skip

    def layout(self, it: LayoutItem) -> None:
        for p in it.field_placements:
            self.place(it.id, it.module, it.layout, p.section, p.field, p.required)

    def workflow(self, it: WorkflowItem) -> None:
        name = _rule_name(it.title)
        if self.live.workflow_named(name):
            return self.skip(it.id, f"workflow {name!r} already exists")
        trigger_field = "Created_Time" if it.trigger == "create" else "Modified_Time"
        action_refs = []
        for a in it.actions:
            if a.field_update:
                fid = self.field_id(it.module, a.field_update.field)
                field = {"api_name": a.field_update.field} | ({"id": fid} if fid else {})
                spec = {"module": {"api_name": it.module}, "type": "static", "feature_type": "workflow",
                        "field": field, "name": f"{it.id} set {a.field_update.field}"[:50], "value": a.field_update.value}  # fmt: skip
                op = self.add(
                    it.id,
                    "create_field_update",
                    it.module,
                    f"field update: {it.module}.{a.field_update.field} = {a.field_update.value}",
                    {"spec": spec},
                )
                action_refs.append({"id": _ref(op), "type": "field_updates"})
            elif a.task:
                spec = {"module": {"api_name": it.module}, "feature_type": "workflow", "name": a.task.subject[:50],
                        "field_mappings": [
                            {"field": {"api_name": "Subject"}, "type": "static", "value": a.task.subject},
                            {"field": {"api_name": "Due_Date"}, "type": "execution_time",
                             "value": {"period": "days", "unit": str(a.task.due_in_days),
                                       "trigger_field": f"${{!{it.module}.{trigger_field}}}", "sign": "plus"}},
                            {"field": {"api_name": "Priority"}, "type": "static", "value": a.task.priority},
                        ]}  # fmt: skip
                op = self.add(
                    it.id,
                    "create_task",
                    it.module,
                    f"task action: {a.task.subject!r} due +{a.task.due_in_days}d",
                    {"spec": spec},
                )
                action_refs.append({"id": _ref(op), "type": "tasks"})
            elif a.type == "function" and a.function:
                fn = self.live.function_by_name(a.function)
                if not fn:
                    return self.error(
                        it.id,
                        f"function {a.function!r} is not in the org yet: push it first (copilot deluge push)",
                    )
                action_refs.append({"id": str(fn["id"]), "type": "functions"})  # EXPERIMENTAL
        group = [
            {"comparator": c.comparator, "field": {"api_name": c.field}, "value": c.value}
            for c in it.conditions
        ]
        rule = {
            "name": name,
            "description": it.rationale[:500],
            "module": {"api_name": it.module},
            "execute_when": {"type": it.trigger, "details": {"trigger_module": {"api_name": it.module}}},
            "conditions": [{
                "sequence_number": 1,
                "criteria_details": {"criteria": {"group_operator": it.condition_operator, "group": group}},
                "instant_actions": {"actions": action_refs},
            }],
            "status": {"active": not it.creates_inactive},
        }  # fmt: skip
        state = "INACTIVE" if it.creates_inactive else "active"
        has_fn = any(a.type == "function" for a in it.actions)
        self.add(
            it.id,
            "create_workflow",
            it.module,
            f"workflow {name!r} on {it.module} ({it.trigger}), created {state}"
            + (" with function attached (experimental)" if has_fn else ""),
            {"rule": rule},
        )


def build_changeset(
    plan: Plan, live: LiveOrg, project: str, org: str, environment: str
) -> ChangeSet:
    cs = ChangeSet(
        id=datetime.now(UTC).strftime("cs-%Y%m%d-%H%M%S"),
        project=project, org=org, environment=environment, plan_version=plan.version,
    )  # fmt: skip
    b = _Builder(plan, live, cs)
    api_items = [i for i in plan.items if plan.delivery.get(i.id) == "api"]
    for i in plan.items:
        if plan.delivery.get(i.id) != "api":
            why = (
                "waiting on a client decision"
                if plan.delivery.get(i.id) == "decision"
                else "no API; handoff pack"
            )
            cs.handoff.append(Skipped(item_id=i.id, reason=f"{i.kind}: {why}"))
    order = (ModuleItem, FieldItem, PicklistChangeItem, LayoutItem, WorkflowItem)
    handlers = {ModuleItem: b.module, FieldItem: b.field, PicklistChangeItem: b.picklist,
                LayoutItem: b.layout, WorkflowItem: b.workflow}  # fmt: skip
    for cls in order:
        for it in api_items:
            if type(it) is cls:
                handlers[cls](it)
    for it in api_items:
        if not isinstance(it, order):
            cs.handoff.append(
                Skipped(
                    item_id=it.id, reason=f"{it.kind}: applied elsewhere (e.g. copilot deluge push)"
                )
            )
    return cs


def render_preview(cs: ChangeSet) -> str:
    out = [
        f"# Change set {cs.id}: {cs.project} (plan v{cs.plan_version}) -> {cs.org} ({cs.environment})",
        "",
    ]
    if cs.errors:
        out += (
            ["## ❌ Problems (nothing will be applied until fixed)", ""]
            + [f"- {e}" for e in cs.errors]
            + [""]
        )
    out += [f"**{len(cs.operations)} operation(s), ~{cs.credits} API credits**", "",
            "| # | Plan item | Operation | Status |", "|---|---|---|---|"]  # fmt: skip
    out += [
        f"| {o.op_id} | {o.item_id} | {o.summary} | {o.status}{(' ' + o.error) if o.error else ''} |"
        for o in cs.operations
    ]
    if cs.skipped:
        out += ["", "## Already done in the org (skipped)", ""] + [
            f"- {s.item_id}: {s.reason}" for s in cs.skipped
        ]
    if cs.handoff:
        out += ["", "## Not applied by the Builder", ""] + [
            f"- {s.item_id}: {s.reason}" for s in cs.handoff
        ]
    out.append("")
    return "\n".join(out)
