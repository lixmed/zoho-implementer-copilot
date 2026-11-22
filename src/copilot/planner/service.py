"""Glue used by both the MCP server and the CLI."""

from __future__ import annotations

import logging
from collections.abc import Callable

from pydantic import ValidationError

from copilot.planner import store
from copilot.planner.schema import Issue, PlanDraft
from copilot.planner.validate import OrgMetadata
from copilot.projects.requirements import load_units
from copilot.projects.workspace import Project
from copilot.zoho import metadata
from copilot.zoho.client import ZohoAPIError, ZohoReadClient

log = logging.getLogger(__name__)


def org_metadata(client: ZohoReadClient | None) -> OrgMetadata:
    if client is None:
        return OrgMetadata(available=False)
    try:
        modules = [m for m in metadata.list_modules(client) if m.get("api_supported")]
        fields = {
            m["api_name"]: {f["api_name"] for f in metadata.get_fields(client, m["api_name"])}
            for m in modules
        }
        return OrgMetadata(fields_by_module=fields)
    except (ZohoAPIError, OSError) as exc:  # network down, scope missing, ...
        log.warning("org metadata unavailable: %s", exc)
        return OrgMetadata(available=False)


def parse_draft(raw: dict) -> tuple[PlanDraft | None, list[Issue]]:
    try:
        return PlanDraft.model_validate(raw), []
    except ValidationError as exc:
        issues = [
            Issue(
                level="error",
                ref=".".join(str(p) for p in e["loc"]),
                message=e["msg"],
            )
            for e in exc.errors(include_url=False)
        ]
        return None, issues


def _summary(issues, coverage) -> dict:
    counts: dict[str, int] = {}
    for c in coverage:
        counts[c.status] = counts.get(c.status, 0) + 1
    return {
        "errors": [i.model_dump() for i in issues if i.level == "error"],
        "warnings": [i.model_dump() for i in issues if i.level == "warning"],
        "coverage": counts,
    }


def check(project: Project, raw: dict, client_factory: Callable[[], ZohoReadClient | None]) -> dict:
    draft, issues = parse_draft(raw)
    if draft is None:
        return {"ok": False, **_summary(issues, [])}
    units = load_units(project.requirements_dir)
    issues, _, coverage = store.check_draft(draft, units, org_metadata(client_factory()))
    return {"ok": not any(i.level == "error" for i in issues), **_summary(issues, coverage)}


def save(project: Project, raw: dict, client_factory: Callable[[], ZohoReadClient | None]) -> dict:
    draft, issues = parse_draft(raw)
    if draft is None:
        return {"saved": False, **_summary(issues, [])}
    units = load_units(project.requirements_dir)
    try:
        plan = store.save_plan(project, draft, units, org_metadata(client_factory()))
    except store.PlanRejectedError as exc:
        return {"saved": False, **_summary(exc.issues, [])}
    return {
        "saved": True,
        "version": plan.version,
        "plan_md": str(project.plans_dir / f"plan.v{plan.version}.md"),
        "next_step": "Ask the human to review plan.md and run: "
        f"copilot plan approve {project.slug} --version {plan.version}",
        **_summary(plan.issues, plan.coverage),
    }
