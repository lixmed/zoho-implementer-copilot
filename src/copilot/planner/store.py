"""Plan versions, approvals and change-request diffs.

Approval is deliberately NOT exposed as an MCP tool: the agent can draft and save
plans, but only a human at the CLI can approve one. Approvals store a SHA-256 of
the plan file, so a plan edited after approval no longer counts as approved.
"""

from __future__ import annotations

import getpass
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from copilot.planner.schema import Plan, PlanDraft
from copilot.planner.validate import OrgMetadata, validate
from copilot.projects.requirements import Unit
from copilot.projects.workspace import Project


class PlanRejectedError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        errors = [i for i in issues if i.level == "error"]
        super().__init__(f"plan has {len(errors)} error(s); nothing saved")


def _plan_path(project: Project, version: int) -> Path:
    return project.plans_dir / f"plan.v{version}.json"


def versions(project: Project) -> list[int]:
    nums = []
    for p in project.plans_dir.glob("plan.v*.json"):
        try:
            nums.append(int(p.stem.split(".v")[1]))
        except (IndexError, ValueError):
            continue
    return sorted(nums)


def load_plan(project: Project, version: int | None = None) -> Plan:
    all_versions = versions(project)
    if not all_versions:
        raise LookupError(f"project {project.slug} has no plans yet")
    v = version or all_versions[-1]
    path = _plan_path(project, v)
    if not path.exists():
        raise LookupError(f"plan v{v} not found (have: {all_versions})")
    return Plan.model_validate_json(path.read_text(encoding="utf-8"))


def check_draft(draft: PlanDraft, units: list[Unit], org: OrgMetadata):
    return validate(draft, units, org)


def save_plan(project: Project, draft: PlanDraft, units: list[Unit], org: OrgMetadata) -> Plan:
    issues, delivery, coverage = validate(draft, units, org)
    if any(i.level == "error" for i in issues):
        raise PlanRejectedError(issues)
    existing = versions(project)
    plan = Plan(
        **draft.model_dump(),
        project=project.slug,
        version=(existing[-1] + 1) if existing else 1,
        based_on_version=existing[-1] if existing else None,
        org=project.info.org,
        delivery=delivery,
        coverage=coverage,
        issues=issues,
    )
    _plan_path(project, plan.version).write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    from copilot.planner.render import render_plan_md  # local import avoids a cycle

    (project.plans_dir / f"plan.v{plan.version}.md").write_text(
        render_plan_md(plan, units), encoding="utf-8"
    )
    return plan


# ---------------- approvals ----------------


class Approval(BaseModel):
    version: int
    approved_at: datetime
    approver: str
    sha256: str
    note: str | None = None


class ApprovalLog(BaseModel):
    approvals: list[Approval] = Field(default_factory=list)


def _approvals_path(project: Project) -> Path:
    return project.plans_dir / "approvals.json"


def _hash(project: Project, version: int) -> str:
    return hashlib.sha256(_plan_path(project, version).read_bytes()).hexdigest()


def load_approvals(project: Project) -> ApprovalLog:
    p = _approvals_path(project)
    return ApprovalLog.model_validate_json(p.read_text("utf-8")) if p.exists() else ApprovalLog()


def approve(project: Project, version: int, note: str | None = None) -> Approval:
    plan = load_plan(project, version)
    blocking = [i for i in plan.issues if i.level == "error"]
    if blocking:
        raise PlanRejectedError(blocking)
    record = Approval(
        version=version,
        approved_at=datetime.now(UTC),
        approver=getpass.getuser(),
        sha256=_hash(project, version),
        note=note,
    )
    log = load_approvals(project)
    log.approvals.append(record)
    _approvals_path(project).write_text(log.model_dump_json(indent=2), encoding="utf-8")
    return record


def approved_version(project: Project) -> int | None:
    """Latest approved version whose file is unchanged since approval."""
    for a in reversed(load_approvals(project).approvals):
        if _plan_path(project, a.version).exists() and _hash(project, a.version) == a.sha256:
            return a.version
    return None


# ---------------- change-request diff ----------------


class ItemChange(BaseModel):
    id: str
    title: str
    fields: dict[str, tuple[object, object]]  # name -> (old, new)
    estimate_delta_minutes: int


class PlanDiff(BaseModel):
    old_version: int
    new_version: int
    added: list[dict] = Field(default_factory=list)
    removed: list[dict] = Field(default_factory=list)
    changed: list[ItemChange] = Field(default_factory=list)
    questions_added: list[str] = Field(default_factory=list)
    questions_resolved: list[str] = Field(default_factory=list)
    tickets_added: list[str] = Field(default_factory=list)
    tickets_removed: list[str] = Field(default_factory=list)
    estimate_delta_minutes: int = 0
    estimate_delta_ticket_hours: float = 0.0


_IGNORED = {"sources"}  # citing more sentences is not a change to what gets built


def diff_plans(old: Plan, new: Plan) -> PlanDiff:
    old_items = {i.id: i.model_dump(mode="json") for i in old.items}
    new_items = {i.id: i.model_dump(mode="json") for i in new.items}
    d = PlanDiff(old_version=old.version, new_version=new.version)

    for iid in new_items.keys() - old_items.keys():
        it = new_items[iid]
        d.added.append({"id": iid, "kind": it["kind"], "title": it["title"],
                        "estimate_minutes": it["estimate_minutes"]})  # fmt: skip
        d.estimate_delta_minutes += it["estimate_minutes"]
    for iid in old_items.keys() - new_items.keys():
        it = old_items[iid]
        d.removed.append({"id": iid, "kind": it["kind"], "title": it["title"],
                          "estimate_minutes": it["estimate_minutes"]})  # fmt: skip
        d.estimate_delta_minutes -= it["estimate_minutes"]
    for iid in sorted(old_items.keys() & new_items.keys()):
        a, b = old_items[iid], new_items[iid]
        changed = {
            k: (a.get(k), b.get(k))
            for k in sorted(a.keys() | b.keys())
            if k not in _IGNORED and a.get(k) != b.get(k)
        }
        if changed:
            delta = b["estimate_minutes"] - a["estimate_minutes"]
            d.changed.append(ItemChange(id=iid, title=b["title"], fields=changed,
                                        estimate_delta_minutes=delta))  # fmt: skip
            d.estimate_delta_minutes += delta
    d.added.sort(key=lambda x: x["id"])
    d.removed.sort(key=lambda x: x["id"])

    oq, nq = {q.id for q in old.questions}, {q.id for q in new.questions}
    d.questions_added, d.questions_resolved = sorted(nq - oq), sorted(oq - nq)
    ot = {t.id: t.estimate_hours for t in old.tickets}
    nt = {t.id: t.estimate_hours for t in new.tickets}
    d.tickets_added, d.tickets_removed = (
        sorted(nt.keys() - ot.keys()),
        sorted(ot.keys() - nt.keys()),
    )
    d.estimate_delta_ticket_hours = sum(nt.values()) - sum(ot.values())
    return d


def plan_json(plan: Plan) -> str:
    return json.dumps(plan.model_dump(mode="json"), indent=2)
