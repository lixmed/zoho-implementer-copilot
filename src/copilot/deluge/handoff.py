"""Deluge function briefs and handoff files.

handoff/
  functions/<name>.dg          header comment (where/how to place it) + the code
  functions/<name>.tests.json  test cases: input -> expected outcome
  functions/<name>.push.json   result of the last push (status / compiler errors)
  connections.md               every connection: service, link name, scopes with reasons
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from copilot.deluge.checks import DECL_KEYWORD, FieldCatalog
from copilot.planner.schema import ConnectionItem, FieldItem, FunctionItem, Plan, WorkflowItem
from copilot.planner.store import approved_version, load_plan
from copilot.planner.validate import OrgMetadata
from copilot.projects.workspace import Project

HEADER_START = "/* COPILOT-HANDOFF"
HEADER_END = "COPILOT-HANDOFF-END */"

MENU_PATH = "Setup > Developer Hub > Functions > + Create New Function"
CATEGORY_LABEL = {
    "automation": "Automation",
    "standalone": "Standalone",
    "button": "Button",
    "schedule": "Schedule",
    "related_list": "Related List",
    "validation": "Validation Rule",
}

CONVENTIONS = """\
House conventions (checked by check_deluge):
- Declaration: <returntype> <category>.<name>(<args>) { ... }  e.g. void automation.post_hot_lead(String lead_id)
- Read records with zoho.crm.getRecordById("<Module>", id) and null-check every value you use.
- External APIs only through invokeurl [ ... connection:"<link_name>" ]; never tokens/keys in code.
- No hard-coded record ids or emails (they differ between sandbox and production).
- Log each step with info, and build a result map: {"status": "ok"|"error", "steps": [...], "error": ...}.
  Standalone/button functions return result.toString(); automation functions log it with info.
- Use the exact field api_names from the brief; never guess a field name.
- Automation functions use CORE + WRAPPER so they can be tested without a workflow:
  * <name>_core: string standalone.<name>_core(String record_id) holds ALL the logic and returns
    result.toString() with a "status" key. This is what the test loop executes.
  * <name>: the automation function is a thin wrapper whose body only calls the core with the
    record id and logs the result. Save both with save_deluge (core name = "<name>_core").
"""

CORE_SUFFIX = "_core"


class TestCase(BaseModel):
    """One test. For record-based tests (automation cores), `record` is created first and its id
    replaces "$record_id" anywhere in `input`; `expect_record` is checked by reading it back."""

    name: str
    input: dict = Field(
        description='arguments for the function; use "$record_id" for the test record id'
    )
    expected: dict = Field(
        description="expected keys/values in the returned result map (subset match)"
    )
    module: str | None = Field(default=None, description="module of the test record, e.g. Leads")
    record: dict | None = Field(default=None, description="fake field values for the test record")
    expect_record: dict | None = Field(
        default=None, description="field values expected on the record afterwards"
    )


class FunctionNotInPlanError(LookupError):
    pass


def handoff_dir(project: Project) -> Path:
    path = project.path / "handoff" / "functions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def plan_for_functions(project: Project) -> Plan:
    """The approved plan if there is one, otherwise the latest draft."""
    return load_plan(project, approved_version(project))


def find_function(plan: Plan, name: str) -> FunctionItem:
    """A planned function, or the testable `<automation>_core` companion of one."""
    for item in plan.items:
        if isinstance(item, FunctionItem) and item.name == name:
            return item
    if name.endswith(CORE_SUFFIX):
        base = find_function(plan, name[: -len(CORE_SUFFIX)])
        if base.category == "automation":
            return base.model_copy(update={
                "name": name, "category": "standalone",
                "purpose": f"core logic of {base.name} (callable by the test loop)",
                "arguments": {"record_id": next(iter(base.arguments.values()), "record id")},
            })  # fmt: skip
    names = [i.name for i in plan.items if isinstance(i, FunctionItem)]
    raise FunctionNotInPlanError(f"function {name!r} is not in plan v{plan.version} (has: {names})")


def field_catalog(plan: Plan, org: OrgMetadata) -> FieldCatalog:
    fields = {m: set(f) for m, f in org.fields_by_module.items()}
    for item in plan.items:
        if isinstance(item, FieldItem):
            fields.setdefault(item.module, set()).add(item.api_name)
    return FieldCatalog(fields_by_module=fields, available=org.available)


def triggers_for(plan: Plan, fn: FunctionItem) -> list[WorkflowItem]:
    return [
        w
        for w in plan.items
        if isinstance(w, WorkflowItem)
        and any(a.type == "function" and fn.name in a.details for a in w.actions)
    ]


def brief(project: Project, name: str, org: OrgMetadata) -> dict:
    plan = plan_for_functions(project)
    fn = find_function(plan, name)
    catalog = field_catalog(plan, org)
    modules = sorted({r.split(".")[0] for r in fn.uses_fields if "." in r})
    connections = [
        c.model_dump(include={"service", "link_name", "scopes"})
        for c in plan.items
        if isinstance(c, ConnectionItem) and c.link_name in fn.connections
    ]
    return {
        "plan_version": plan.version,
        "function": fn.model_dump(),
        "declaration_keyword": DECL_KEYWORD[fn.category],
        "fields": {m: sorted(catalog.fields_by_module.get(m, set())) for m in modules},
        "connections": connections,
        "triggered_by": [
            w.model_dump(include={"id", "module", "trigger", "criteria"})
            for w in triggers_for(plan, fn)
        ],
        "conventions": CONVENTIONS,
        "docs_hint": "search_deluge_docs(query, app) then read_deluge_doc(url) for exact task syntax",
    }


def render_header(plan: Plan, fn: FunctionItem) -> str:
    triggers = triggers_for(plan, fn)
    lines = [
        HEADER_START,
        f"  Function    : {fn.name}   (plan {fn.id}, v{plan.version})",
        f"  Purpose     : {fn.purpose}",
        f"  Where       : {MENU_PATH}",
        f"  Category    : {CATEGORY_LABEL[fn.category]}",
        "  Arguments   : "
        + (", ".join(f"{a} <- {src}" for a, src in fn.arguments.items()) or "none"),
        "  Connections : " + (", ".join(fn.connections) or "none"),
    ]
    if triggers:
        for w in triggers:
            lines.append(
                f"  Trigger     : workflow {w.id} on {w.module} ({w.trigger}) when {w.criteria}"
            )
    else:
        lines.append("  Trigger     : none in the plan")
    lines += [
        "  Attach      : NOT attached automatically. Associate it with the workflow rule",
        "                only after the change is approved (handoff checklist).",
        HEADER_END,
    ]
    return "\n".join(lines)


def strip_header(text: str) -> str:
    start = text.find(HEADER_START)
    end = text.find(HEADER_END)
    if start != -1 and end != -1:
        return (text[:start] + text[end + len(HEADER_END) :]).strip() + "\n"
    return text


def save_function(project: Project, name: str, code: str, tests: list[TestCase]) -> dict:
    plan = plan_for_functions(project)
    fn = find_function(plan, name)
    code = strip_header(code)
    folder = handoff_dir(project)
    dg = folder / f"{name}.dg"
    dg.write_text(render_header(plan, fn) + "\n" + code, encoding="utf-8")
    (folder / f"{name}.tests.json").write_text(
        json.dumps([t.model_dump() for t in tests], indent=2), encoding="utf-8"
    )
    write_connections_md(project, plan)
    return {"saved": str(dg), "tests": len(tests)}


def load_function_code(project: Project, name: str) -> str:
    dg = handoff_dir(project) / f"{name}.dg"
    if not dg.exists():
        raise LookupError(f"no saved code for {name!r}; save it first ({dg})")
    return strip_header(dg.read_text(encoding="utf-8"))


def write_connections_md(project: Project, plan: Plan) -> Path:
    conns = [c for c in plan.items if isinstance(c, ConnectionItem)]
    users: dict[str, list[str]] = {}
    for f in plan.items:
        if isinstance(f, FunctionItem):
            for link in f.connections:
                users.setdefault(link, []).append(f.name)
    out = [f"# Connections: {project.slug} (plan v{plan.version})", ""]
    if not conns:
        out.append("No connections in this plan.")
    for c in conns:
        out += [
            f"## {c.connection_name}",
            "",
            f"- Service: {c.service}",
            f"- Link name: `{c.link_name}` (must match the code exactly)",
            f"- Used by: {', '.join(users.get(c.link_name, [])) or 'nothing yet'}",
            "- Create in: Setup > Developer Hub > Connections > Create Connection "
            "(the client's admin authorizes it)",
            "- Minimum scopes:",
            *[f"  - `{s.scope}`: {s.reason}" for s in c.scopes],
            "",
        ]
    path = project.path / "handoff" / "connections.md"
    path.write_text("\n".join(out), encoding="utf-8")
    return path


def record_push(project: Project, name: str, result: dict) -> None:
    result = {**result, "at": datetime.now(UTC).isoformat()}
    (handoff_dir(project) / f"{name}.push.json").write_text(json.dumps(result, indent=2), "utf-8")


def last_push(project: Project, name: str) -> dict | None:
    p = handoff_dir(project) / f"{name}.push.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
