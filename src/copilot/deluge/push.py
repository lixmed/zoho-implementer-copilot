"""Push a saved Deluge function to a developer/sandbox org, only with human approval.

Gates, in order (any failure = nothing is sent):
  1. project is linked to an org, and that org is developer or sandbox
  2. the project has an APPROVED plan (unchanged since approval) containing the function
  3. the org was connected with function write scopes (--allow-writes)
  4. local static checks pass against live metadata (no errors)
  5. the human types the function name to confirm
The function is saved in Zoho but NOT attached to any workflow.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from copilot.audit.log import AuditLog
from copilot.deluge import handoff
from copilot.deluge.checks import check_deluge
from copilot.orgs.registry import OrgRegistry
from copilot.orgs.session import open_read_client
from copilot.planner.service import org_metadata
from copilot.planner.store import approved_version, load_plan
from copilot.projects.workspace import Project
from copilot.zoho.write import FUNCTION_WRITE_SCOPES, WriteRefusedError, ZohoWriteClient


class PushRefusedError(PermissionError):
    pass


def _has_scope(scopes: list[str], needed: str) -> bool:
    return (
        needed in scopes
        or "ZohoCRM.settings.functions.ALL" in scopes
        or "ZohoCRM.settings.ALL" in scopes
    )


def push_function(
    project: Project,
    name: str,
    confirm: Callable[[str], bool],
    http: httpx.Client | None = None,
    audit: AuditLog | None = None,
) -> dict:
    if not project.info.org:
        raise PushRefusedError("project has no linked org (copilot project new ... --org <slug>)")
    org = OrgRegistry().get(project.info.org)
    if org.environment not in ("developer", "sandbox"):
        raise PushRefusedError(
            f"org {org.slug} is {org.environment}; pushes go to developer/sandbox only"
        )

    version = approved_version(project)
    if version is None:
        raise PushRefusedError(
            "no approved plan (or it was edited after approval): run copilot plan approve"
        )
    plan = load_plan(project, version)
    fn = handoff.find_function(plan, name)

    missing = [s for s in FUNCTION_WRITE_SCOPES if not _has_scope(org.scopes, s)]
    if missing:
        raise PushRefusedError(
            f"org {org.slug} was connected read-only (missing {', '.join(missing)}). Reconnect with: "
            f"copilot connect --name '{org.name}' --slug {org.slug} --env {org.environment} --allow-writes"
        )

    code = handoff.load_function_code(project, name)
    _, reader = open_read_client(org.slug, http=http)
    catalog = handoff.field_catalog(plan, org_metadata(reader))
    issues = check_deluge(code, fn, catalog)
    errors = [i for i in issues if i.level == "error"]
    if errors:
        raise PushRefusedError(
            "static checks failed: " + "; ".join(f"{i.ref}: {i.message}" for i in errors)
        )

    existing = next(
        (f for f in reader.get("/settings/functions", use_cache=False).get("functions", [])
         if f.get("api_name") == name),
        None,
    )  # fmt: skip
    action = "UPDATE" if existing else "CREATE"
    if not confirm(
        f"{action} function '{name}' in {org.name} ({org.environment}, {org.dc}) from plan v{version}. "
        f"It will NOT be attached to any workflow. Type the function name to confirm: "
    ):
        return {"pushed": False, "reason": "not confirmed"}

    try:
        writer = ZohoWriteClient(
            org, reader.tokens, audit or AuditLog(), http=http,
            project=project.slug, plan_version=version,
        )  # fmt: skip
    except WriteRefusedError as exc:
        raise PushRefusedError(str(exc)) from None

    if existing:
        before = {
            k: existing.get(k) for k in ("id", "api_name", "name", "category", "modified_time")
        }
        result = writer.update_function(
            existing["id"], name, code, f"copilot: plan v{version} {fn.id}", before
        )
    else:
        label = handoff.CATEGORY_LABEL[fn.category]
        result = writer.create_function(name, label, code, fn.purpose)

    outcome = {
        "pushed": result.ok,
        "action": action.lower(),
        "org": org.slug,
        "plan_version": version,
        "code": result.get("code"),
        "message": result.get("message"),
        "details": result.get("details"),
        "attached_to_workflow": False,
    }
    handoff.record_push(project, name, outcome)
    return outcome
