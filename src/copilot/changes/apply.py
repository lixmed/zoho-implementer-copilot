"""Apply a change set (human-confirmed) and roll it back (soft by default).

Gates before anything is sent (same spirit as `deluge push`):
  developer/sandbox/test org only · approved plan unchanged since approval · no build errors ·
  org has the needed write scopes · the human types the project slug.
On the first failure: stop, save state, offer rollback of what was applied.
Rollback is SOFT by default (hide/deactivate, never delete). --hard deletes created fields
and workflows after a separate typed confirmation, because deleting a field deletes its data.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from copilot.audit.log import AuditLog
from copilot.changes.build import ChangeSet, Operation, build_changeset, render_preview
from copilot.changes.live import LiveOrg
from copilot.orgs.registry import OrgRegistry
from copilot.orgs.session import open_read_client
from copilot.planner.store import approved_version, load_plan
from copilot.projects.workspace import Project
from copilot.zoho.write import ZohoWriteClient, ZohoWriteResult

WRITABLE_ENVS = ("developer", "sandbox", "test")


class ApplyRefusedError(PermissionError):
    pass


# ---------------------------------------------------------------- storage


def changesets_dir(project: Project) -> Path:
    p = project.path / "changesets"
    p.mkdir(parents=True, exist_ok=True)
    return p


def save(project: Project, cs: ChangeSet, **extra: Any) -> Path:
    folder = changesets_dir(project) / cs.id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "changeset.json").write_text(cs.model_dump_json(indent=2), encoding="utf-8")
    (folder / "changes.md").write_text(render_preview(cs), encoding="utf-8")
    for name, data in extra.items():
        (folder / f"{name}.json").write_text(
            json.dumps(data, indent=2, default=str), encoding="utf-8"
        )
    return folder


def load(project: Project, cs_id: str | None = None) -> ChangeSet:
    folders = sorted(
        p for p in changesets_dir(project).iterdir() if (p / "changeset.json").exists()
    )
    if not folders:
        raise LookupError("no change sets in this project yet")
    folder = changesets_dir(project) / cs_id if cs_id else folders[-1]
    if not (folder / "changeset.json").exists():
        raise LookupError(f"change set {cs_id} not found (have: {[f.name for f in folders]})")
    return ChangeSet.model_validate_json((folder / "changeset.json").read_text(encoding="utf-8"))


def list_changesets(project: Project) -> list[ChangeSet]:
    return [
        ChangeSet.model_validate_json((p / "changeset.json").read_text("utf-8"))
        for p in sorted(changesets_dir(project).iterdir())
        if (p / "changeset.json").exists()
    ]


# ---------------------------------------------------------------- helpers


def _org_and_reader(project: Project, http: httpx.Client | None):
    if not project.info.org:
        raise ApplyRefusedError("project has no linked org (copilot project new ... --org <slug>)")
    org = OrgRegistry().get(project.info.org)
    if org.environment not in WRITABLE_ENVS:
        raise ApplyRefusedError(
            f"org {org.slug} is {org.environment}; the Builder writes to {', '.join(WRITABLE_ENVS)} only"
        )
    _, reader = open_read_client(org.slug, http=http)
    return org, reader


def preview(project: Project, http: httpx.Client | None = None) -> ChangeSet:
    """Read-only: what would change if the latest approved plan were applied now."""
    org, reader = _org_and_reader(project, http)
    version = approved_version(project)
    if version is None:
        raise ApplyRefusedError(
            "no approved plan (or it was edited after approval): run copilot plan approve"
        )
    return build_changeset(
        load_plan(project, version), LiveOrg(reader), project.slug, org.slug, org.environment
    )


def _resolve(value: Any, created: dict[str, str]) -> Any:
    if isinstance(value, str) and value.startswith("$op:"):
        op_id = value[4:]
        if op_id not in created:
            raise RuntimeError(f"operation {op_id} has no created id yet")
        return created[op_id]
    if isinstance(value, dict):
        return {k: _resolve(v, created) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, created) for v in value]
    return value


def _dispatch(w: ZohoWriteClient, op: Operation, req: dict) -> ZohoWriteResult:
    k = op.kind
    if k == "create_module":
        return w.create_module(req["module"])
    if k == "create_field":
        return w.create_fields(req["module"], req["fields"])
    if k in ("picklist_add", "picklist_rename"):
        return w.update_field(req["module"], req["field_id"], req["changes"])
    if k in ("picklist_retire", "place_field"):
        return w.patch_layout(req["module"], req["layout_id"], req["layout"])
    if k == "create_field_update":
        return w.create_field_update(req["spec"])
    if k == "create_task":
        return w.create_task(req["spec"])
    if k == "create_workflow":
        return w.create_workflow(req["rule"])
    raise ValueError(k)


def _created_id(res: ZohoWriteResult) -> str | None:
    details = res.get("details")
    return str(details["id"]) if isinstance(details, dict) and details.get("id") else None


def summarize_diff(before: dict, after: dict) -> dict:
    out: dict = {
        "fields_added": {},
        "fields_removed": {},
        "workflows_added": [],
        "workflows_removed": [],
    }
    for mod in set(before.get("fields", {})) | set(after.get("fields", {})):
        b, a = set(before.get("fields", {}).get(mod, {})), set(after.get("fields", {}).get(mod, {}))
        if a - b:
            out["fields_added"][mod] = sorted(a - b)
        if b - a:
            out["fields_removed"][mod] = sorted(b - a)
    bw = {w.get("name") for w in before.get("workflow_rules", [])}
    aw = {w.get("name") for w in after.get("workflow_rules", [])}
    out["workflows_added"], out["workflows_removed"] = sorted(aw - bw), sorted(bw - aw)
    return out


# ---------------------------------------------------------------- apply


def apply(
    project: Project,
    confirm: Callable[[str], bool],
    offer_rollback: Callable[[ChangeSet], bool],
    http: httpx.Client | None = None,
    audit: AuditLog | None = None,
) -> ChangeSet:
    org, reader = _org_and_reader(project, http)
    version = approved_version(project)
    if version is None:
        raise ApplyRefusedError(
            "no approved plan (or it was edited after approval): run copilot plan approve"
        )
    live = LiveOrg(reader)
    cs = build_changeset(load_plan(project, version), live, project.slug, org.slug, org.environment)
    if cs.errors:
        raise ApplyRefusedError("change set has problems: " + "; ".join(cs.errors))
    if not cs.operations:
        return cs
    missing = sorted(
        s for s in cs.scopes_needed() if s not in org.scopes and not _covered(s, org.scopes)
    )
    if missing:
        raise ApplyRefusedError(
            f"org {org.slug} lacks write scopes {missing}. Reconnect with --allow-writes "
            f"(copilot connect --name '{org.name}' --slug {org.slug} --env {org.environment} --allow-writes)"
        )
    touched = sorted({o.module for o in cs.operations})
    before = live.snapshot(touched)
    save(project, cs, before=before)
    if not confirm(render_preview(cs) + f"\nType the project slug ({project.slug}) to apply: "):
        return cs

    writer = ZohoWriteClient(
        org,
        reader.tokens,
        audit or AuditLog(),
        http=http,
        project=project.slug,
        plan_version=version,
    )
    created: dict[str, str] = {}
    failed = False
    for op in cs.operations:
        if failed:
            op.status = "not_run"
            continue
        try:
            res = _dispatch(writer, op, _resolve(op.request, created))
        except Exception as exc:  # noqa: BLE001 (network errors etc. must stop the run cleanly)
            res = ZohoWriteResult(status="error", code=type(exc).__name__, message=str(exc))
        if res.ok:
            op.status = "applied"
            op.created_id = _created_id(res)
            if op.created_id:
                created[op.op_id] = op.created_id
        else:
            op.status, failed = "failed", True
            op.error = f"{res.get('code')}: {res.get('message')}"
    cs.status = "partial" if failed else "applied"
    reader.clear_cache()
    live.refresh(touched)
    after = live.snapshot(touched)
    save(project, cs, before=before, after=after, diff=summarize_diff(before, after))
    if failed and any(o.status == "applied" for o in cs.operations) and offer_rollback(cs):
        rollback(project, cs.id, hard=False, http=http, audit=audit)
        return load(project, cs.id)
    return cs


def _covered(scope: str, granted: list[str]) -> bool:
    family = scope.rsplit(".", 1)[0]
    return f"{family}.ALL" in granted or "ZohoCRM.settings.ALL" in granted


# ---------------------------------------------------------------- rollback


def rollback(
    project: Project,
    cs_id: str | None = None,
    hard: bool = False,
    confirm_hard: Callable[[str], bool] | None = None,
    http: httpx.Client | None = None,
    audit: AuditLog | None = None,
) -> ChangeSet:
    cs = load(project, cs_id)
    applied = [o for o in cs.operations if o.status == "applied"]
    if not applied:
        return cs
    if hard and not (confirm_hard and confirm_hard(
        f"HARD rollback of {cs.id} DELETES {sum(o.kind == 'create_field' for o in applied)} field(s) "
        "and ALL DATA in them, plus created workflow rules and actions. This cannot be undone. Type DELETE: "
    )):  # fmt: skip
        hard = False
    org, reader = _org_and_reader(project, http)
    live = LiveOrg(reader)
    w = ZohoWriteClient(
        org,
        reader.tokens,
        audit or AuditLog(),
        http=http,
        project=project.slug,
        plan_version=cs.plan_version,
    )
    for op in reversed(applied):
        try:
            res = _undo(w, live, op, hard)
        except Exception as exc:  # noqa: BLE001
            res = ZohoWriteResult(status="error", code=type(exc).__name__, message=str(exc))
        if res is None:
            op.status = "manual"
            op.error = "no API to undo this; see the handoff checklist"
        elif res.ok:
            op.status = "rolled_back"
        else:
            op.status = "rollback_failed"
            op.error = f"{res.get('code')}: {res.get('message')}"
    ok = all(o.status in ("rolled_back",) for o in applied)
    cs.status = "rolled_back" if ok else "rolled_back_partial"
    save(project, cs)
    return cs


_OK = ZohoWriteResult(status="success")


def _keep_list_patch(
    w, live: LiveOrg, module: str, field_id: str, keep: list[dict]
) -> ZohoWriteResult:
    res = _OK
    for lay in live.layouts_containing(module, field_id, fresh=True):
        sec, _ = live.find_field_in_layout(lay, field_id)
        layout = {"id": str(lay["id"]), "sections": [{"id": str(sec["id"]), "fields": [{"id": field_id,
                  "pick_list_values": [{"display_value": v["display_value"], "id": v["id"]} for v in keep]}]}]}  # fmt: skip
        res = w.patch_layout(module, str(lay["id"]), layout, before={"keep": keep})
        if not res.ok:
            return res
    return res


def _undo(w: ZohoWriteClient, live: LiveOrg, op: Operation, hard: bool) -> ZohoWriteResult | None:
    k, mod = op.kind, op.module
    if k == "create_module":
        return None  # Zoho has no module delete API
    if k == "create_field":
        if hard:
            return w.delete_field(mod, op.created_id)
        res = _OK
        for lay in live.layouts_containing(mod, op.created_id, fresh=True):
            sec, _ = live.find_field_in_layout(lay, op.created_id)
            layout = {
                "id": str(lay["id"]),
                "sections": [
                    {
                        "id": str(sec["id"]),
                        "fields": [{"id": op.created_id, "_delete": {"permanent": False}}],
                    }
                ],
            }
            res = w.patch_layout(mod, str(lay["id"]), layout)
            if not res.ok:
                return res
        return res
    if k in ("picklist_add", "picklist_retire"):
        return _keep_list_patch(w, live, mod, op.undo["field_id"], op.undo["before_values"])
    if k == "picklist_rename":
        back = [{"display_value": v["display_value"], "actual_value": v["actual_value"], "id": v["id"]}
                for v in op.undo["before_values"]
                if v["id"] in {r["id"] for r in op.request["changes"]["pick_list_values"]}]  # fmt: skip
        return w.update_field(mod, op.undo["field_id"], {"pick_list_values": back})
    if k == "place_field":
        fid = op.request["layout"]["sections"][0]["fields"][0]["id"]
        lid = op.undo["layout_id"]
        if op.undo.get("was_in_layout"):
            field = {"id": fid, "required": op.undo["required"]}
            layout = {"id": lid, "sections": [{"id": op.undo["section_id"], "fields": [field]}]}
        else:
            if fid.startswith("$op:"):
                return _OK  # the field itself is undone by its create_field operation
            layout = {
                "id": lid,
                "sections": [
                    {
                        "id": op.request["layout"]["sections"][0]["id"],
                        "fields": [{"id": fid, "_delete": {"permanent": False}}],
                    }
                ],
            }
        return w.patch_layout(mod, lid, layout)
    if k == "create_field_update":
        return w.delete_field_update(op.created_id) if hard else _OK  # unused once its rule is off
    if k == "create_task":
        return w.delete_task(op.created_id) if hard else _OK
    if k == "create_workflow":
        return (
            w.delete_workflow(op.created_id)
            if hard
            else w.set_workflow_active(op.created_id, False)
        )
    raise ValueError(k)
