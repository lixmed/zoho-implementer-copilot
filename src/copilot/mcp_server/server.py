"""Local MCP server exposing Zoho CRM metadata as READ-ONLY tools.

Tier: read. Every tool here is annotated read_only_hint=True and goes through
ZohoReadClient, which has no write methods. Write and danger tiers arrive in Phase 4.

Run:  copilot serve   (stdio transport; never print to stdout here)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from copilot.orgs.registry import OrgRegistry
from copilot.orgs.session import open_read_client
from copilot.zoho import metadata
from copilot.zoho.client import ZohoReadClient

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
Tools for reading Zoho CRM org METADATA (structure, not customer records).
Always call list_orgs first and pass the org slug explicitly to every other tool.
All tools are read-only. Customer records are not accessible by design, and user
identities are masked (user_1, user_2, ...). Use field api_name values exactly as returned.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)

server = MCPServer("zoho-implementer-copilot", instructions=INSTRUCTIONS)
_clients: dict[str, ZohoReadClient] = {}


def _client(org: str) -> ZohoReadClient:
    if org not in _clients:
        _, _clients[org] = open_read_client(org)
    return _clients[org]


def _read_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    return server.tool(annotations=READ_ONLY)(fn)


@_read_tool
def list_orgs() -> list[dict]:
    """List connected Zoho orgs (slug, name, environment, data centre). No secrets."""
    return [
        o.model_dump(include={"slug", "name", "environment", "dc", "edition", "zoho_org_id"})
        for o in OrgRegistry().list()
    ]


@_read_tool
def get_org_info(org: str) -> dict:
    """Company-level settings of the org (country, time zone, currency). Contact details removed."""
    return metadata.get_org(_client(org))


@_read_tool
def get_modules(org: str) -> list[dict]:
    """All modules with api_name, labels, and whether they are custom (generated_type)."""
    return metadata.list_modules(_client(org))


@_read_tool
def get_fields(org: str, module: str) -> list[dict]:
    """Fields of one module (by module api_name): api_name, label, data type, picklists, lookups."""
    return metadata.get_fields(_client(org), module)


@_read_tool
def get_picklist_values(org: str, module: str, field_id: str) -> list[dict]:
    """Used and unused options of one picklist field, including layout associations."""
    return metadata.get_picklist_values(_client(org), module, field_id)


@_read_tool
def get_layouts(org: str, module: str) -> list[dict]:
    """Layouts of a module: sections, which fields are placed where, required flags, profiles."""
    return metadata.get_layouts(_client(org), module)


@_read_tool
def get_related_lists(org: str, module: str) -> list[dict]:
    """Related lists shown on a module's records."""
    return metadata.get_related_lists(_client(org), module)


@_read_tool
def get_workflow_rules(org: str) -> list[dict]:
    """Workflow rules across modules: name, module, status, trigger and conditions."""
    return metadata.list_workflow_rules(_client(org))


@_read_tool
def get_blueprints(org: str) -> list[dict]:
    """Blueprint configurations (states and transitions)."""
    return metadata.list_blueprints(_client(org))


@_read_tool
def get_functions(org: str) -> list[dict]:
    """Deluge custom functions: api_name, category, REST API mode. (Code is not fetched.)"""
    return metadata.list_functions(_client(org))


@_read_tool
def get_roles(org: str) -> list[dict]:
    """Role hierarchy."""
    return metadata.list_roles(_client(org))


@_read_tool
def get_profiles(org: str) -> list[dict]:
    """Permission profiles."""
    return metadata.list_profiles(_client(org))


@_read_tool
def get_users(org: str) -> list[dict]:
    """Users with identities MASKED (alias, id, role, profile, status only)."""
    return metadata.list_users_masked(_client(org))


# ---------------------------------------------------------------------------
# Planner tier: reads requirements, writes plan files to the LOCAL workspace only.
# Nothing here touches Zoho except metadata reads for validation. There is no
# approve tool on purpose: approval is a human action at the CLI.
# ---------------------------------------------------------------------------

LOCAL_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)


def _project(slug: str):
    from copilot.projects.workspace import open_project

    return open_project(slug)


def _client_factory(project):
    def factory():
        return _client(project.info.org) if project.info.org else None

    return factory


@_read_tool
def list_projects() -> list[dict]:
    """Client projects in the workspace (slug, client, project, linked org)."""
    from copilot.projects.workspace import list_projects as _list

    return [p.info.model_dump() for p in _list()]


@_read_tool
def get_requirements(project: str) -> list[dict]:
    """Requirement units for a project. Each has an id (e.g. 'kickoff:3') that plan items
    MUST cite in `sources`. Contact details are masked."""
    from copilot.projects.requirements import load_units

    return [u.model_dump(exclude_none=True) for u in load_units(_project(project).requirements_dir)]


@_read_tool
def get_plan_schema() -> dict:
    """JSON Schema of the plan draft accepted by check_plan / save_plan."""
    from copilot.planner.schema import PlanDraft

    return PlanDraft.model_json_schema()


@_read_tool
def get_plan(project: str, version: int | None = None) -> dict:
    """A saved plan version (latest if version is omitted), with computed coverage."""
    from copilot.planner.store import load_plan

    return load_plan(_project(project), version).model_dump(mode="json")


@_read_tool
def check_plan(project: str, plan: dict) -> dict:
    """Validate a plan draft WITHOUT saving: schema, traceability, api names vs the org,
    scopes. Returns errors, warnings and computed coverage. Fix every error."""
    from copilot.planner import service

    p = _project(project)
    return service.check(p, plan, _client_factory(p))


@server.tool(annotations=LOCAL_WRITE)
def save_plan(project: str, plan: dict) -> dict:
    """Validate and save a plan draft as the next version (plan.vN.json + plan.vN.md) in the
    local workspace. Refuses if there are errors. Does NOT approve or apply anything."""
    from copilot.planner import service

    p = _project(project)
    return service.save(p, plan, _client_factory(p))


@_read_tool
def diff_plans(project: str, old_version: int, new_version: int) -> dict:
    """Change-request diff between two plan versions: items added/removed/changed, effort delta."""
    from copilot.planner.store import diff_plans as _diff
    from copilot.planner.store import load_plan

    p = _project(project)
    return _diff(load_plan(p, old_version), load_plan(p, new_version)).model_dump(mode="json")


# ---------------------------------------------------------------------------
# Deluge tier: docs lookup (public zoho.com docs only), checks, local saving.
# There is NO push tool: saving a function in Zoho is a human CLI action
# (`copilot deluge push`), and attaching it to a workflow is never automated.
# ---------------------------------------------------------------------------

DOCS_READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)


@server.tool(annotations=DOCS_READ)
def search_deluge_docs(query: str, app: str = "crm") -> list[dict]:
    """Search Zoho's official Deluge docs for one app (crm, books, creator, desk, people,
    projects, ...). Ranking happens locally; only public zoho.com doc pages are fetched."""
    from copilot.deluge import docs

    return [h.__dict__ for h in docs.search(query, app)]


@server.tool(annotations=DOCS_READ)
def read_deluge_doc(url: str) -> dict:
    """Read one official Zoho docs page (www.zoho.com/deluge/help/... or /<app>/developer|help/...)
    as text with code samples. Use it for exact task syntax before writing code."""
    from copilot.deluge import docs

    return docs.read_doc(url)


def _function_ctx(project: str):
    from copilot.planner.service import org_metadata

    p = _project(project)
    return p, org_metadata(_client_factory(p)())


@_read_tool
def get_function_brief(project: str, name: str) -> dict:
    """Everything needed to write one planned Deluge function: the plan item, exact field
    api_names of the modules it uses, its connections, its triggers, and house conventions."""
    from copilot.deluge import handoff

    p, org = _function_ctx(project)
    return handoff.brief(p, name, org)


@_read_tool
def check_deluge(project: str, name: str, code: str) -> dict:
    """Static checks: declaration, brackets, secrets, connections, module/field api names vs
    the org, null checks, logging. Fix every error before saving."""
    from copilot.deluge import handoff
    from copilot.deluge.checks import check_deluge as _check

    p, org = _function_ctx(project)
    plan = handoff.plan_for_functions(p)
    fn = handoff.find_function(plan, name)
    issues = _check(handoff.strip_header(code), fn, handoff.field_catalog(plan, org))
    return {
        "ok": not any(i.level == "error" for i in issues),
        "errors": [i.model_dump() for i in issues if i.level == "error"],
        "warnings": [i.model_dump() for i in issues if i.level == "warning"],
    }


@server.tool(annotations=LOCAL_WRITE)
def save_deluge(project: str, name: str, code: str, tests: list[dict]) -> dict:
    """Save a checked function to the project's handoff folder (<name>.dg with a placement
    header, <name>.tests.json, connections.md). Refuses if checks have errors. Does not push."""
    from copilot.deluge import handoff
    from copilot.deluge.checks import check_deluge as _check

    p, org = _function_ctx(project)
    plan = handoff.plan_for_functions(p)
    fn = handoff.find_function(plan, name)
    issues = _check(handoff.strip_header(code), fn, handoff.field_catalog(plan, org))
    errors = [i.model_dump() for i in issues if i.level == "error"]
    if errors:
        return {"saved": False, "errors": errors}
    out = handoff.save_function(p, name, code, [handoff.TestCase(**t) for t in tests])
    return {
        **out,
        "warnings": [i.model_dump() for i in issues if i.level == "warning"],
        "next_step": f"Human pushes it to the dev org with: copilot deluge push {p.slug} {name}",
    }


@_read_tool
def get_push_result(project: str, name: str) -> dict:
    """Result of the last push of a function (success, or Zoho's compiler error to fix)."""
    from copilot.deluge import handoff

    return handoff.last_push(_project(project), name) or {"pushed": None, "message": "never pushed"}


@_read_tool
def get_test_results(project: str, name: str) -> dict:
    """Latest test run of a function (per case: pass/fail, problems, output, compared record
    fields) plus its attempt history. Fix failures, then save_deluge; the human's
    `copilot deluge loop` pushes and re-runs."""
    import json as _json

    from copilot.deluge import handoff
    from copilot.deluge.testloop import history

    p = _project(project)
    path = handoff.handoff_dir(p) / f"{name}.testrun.json"
    run = _json.loads(path.read_text("utf-8")) if path.exists() else None
    return {"latest": run, "attempts": len(history(p, name))}


@_read_tool
def preview_changes(project: str) -> dict:
    """What applying the latest APPROVED plan would change in the org right now: ordered
    operations, API credits, items already done (skipped) and items left as handoff.
    Read-only. Applying is a human CLI action (`copilot apply`)."""
    from copilot.changes.apply import preview

    cs = preview(_project(project))
    return {
        "operations": [
            o.model_dump(include={"op_id", "item_id", "kind", "summary", "credits"})
            for o in cs.operations
        ],
        "credits": cs.credits,
        "skipped": [s.model_dump() for s in cs.skipped],
        "handoff": [s.model_dump() for s in cs.handoff],
        "errors": cs.errors,
        "next_step": f"Human reviews and runs: copilot apply {project}",
    }


PLAN_PROMPT = """\
You are helping a Zoho CRM implementer turn client requirements into an implementation plan.

Project: {project}

Steps:
1. get_requirements("{project}") and read every unit.
2. If the project has a linked org: get_modules, then get_fields / get_layouts /
   get_workflow_rules / get_functions for the relevant modules. Reuse existing fields;
   never invent api_names for fields you claim already exist.
3. get_plan_schema(). If a previous plan exists (get_plan), revise it and KEEP the ids of
   unchanged items, so the change-request diff is meaningful.
4. Draft the plan:
   - Every item, question and ticket cites the unit ids it comes from in `sources`.
   - Every unit must be accounted for: cited, asked about, or listed in out_of_scope with a reason.
   - Ask a client question instead of guessing when a requirement is ambiguous.
   - Connections: minimum scopes, each with a reason.
   - Things with no API (validation rules, buttons, blueprints, related lists, new layouts) are
     UiOnly items with steps_hint.
   - Custom development beyond configuration becomes a developer ticket.
5. check_plan until there are no errors, then save_plan.
6. Tell the human where plan.md is and that they must approve it themselves with
   `copilot plan approve`. You cannot approve plans.
"""


@server.prompt(name="plan_project", description="Draft or revise the implementation plan")
def plan_project(project: str) -> str:
    return PLAN_PROMPT.format(project=project)


DELUGE_PROMPT = """\
Write the Deluge function `{name}` for project {project}.

1. get_function_brief("{project}", "{name}"): use ONLY the field api_names it lists.
2. For every Deluge task you use (zoho.crm.*, invokeurl, map/list functions), look up the exact
   syntax: search_deluge_docs("<task>", "crm") then read_deluge_doc(url). Don't rely on memory.
3. Follow the conventions in the brief (declaration, null checks, info logs, result map,
   connections by link name, no secrets or hard-coded ids/emails).
4. check_deluge until there are no errors, then save_deluge with 2-4 test cases
   (input record values -> expected outcome), including one null/missing-value case.
5. Tell the human to push it with `copilot deluge push`. You cannot push, and the function
   must NOT be attached to any workflow until they approve that separately.
6. After a push, get_push_result: if Zoho reports a compilation error, fix the code at the
   reported line, check, save again, and ask the human to push again.
7. Automation functions: write BOTH `<name>_core` (standalone, all logic, takes record_id,
   returns the result map) and the thin `<name>` wrapper. Record-based tests on the core use
   module + record (fake values) + input {"record_id": "$record_id"} + expect_record.
8. Tests: the human runs `copilot deluge loop`. When a run fails, get_test_results, fix the
   code, check_deluge, save_deluge (same tests). The loop shows them the diff and re-runs.
"""


@server.prompt(name="write_deluge", description="Write, check and save one planned Deluge function")
def write_deluge(project: str, name: str) -> str:
    return DELUGE_PROMPT.format(project=project, name=name)


def run() -> None:
    logging.basicConfig(level=logging.INFO)  # basicConfig logs to stderr; stdout belongs to MCP
    server.run("stdio")
