"""Client project workspaces, in a folder the user chooses (outside the git repo).

<root>/<client>/<project>/
    project.json          client, project, linked org slug
    requirements/         raw files from the client (.md .txt .eml .docx .pdf)
    requirements/.units.json   extracted, addressable requirement units (generated)
    plans/plan.vN.json    machine-readable plan versions
    plans/plan.vN.md      human-readable rendering
    plans/approvals.json  who approved which version, with a content hash
    exports/              sign-off doc, UAT script, change requests
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel

from copilot.orgs.registry import home_dir, slugify


class ProjectInfo(BaseModel):
    client: str
    project: str
    slug: str  # "<client-slug>/<project-slug>"
    org: str | None = None


class WorkspaceNotConfiguredError(LookupError):
    pass


def _config_path() -> Path:
    return home_dir() / "config.json"


def _read_config() -> dict:
    p = _config_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def set_workspace_root(path: Path) -> Path:
    path = path.expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    cfg = _read_config()
    cfg["workspace_root"] = str(path)
    _config_path().write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return path


def workspace_root() -> Path:
    if env := os.environ.get("COPILOT_PROJECTS"):
        return Path(env)
    root = _read_config().get("workspace_root")
    if not root:
        raise WorkspaceNotConfiguredError(
            "no projects folder set. Run: copilot workspace set C:/path/to/ZohoProjects"
        )
    return Path(root)


class Project:
    def __init__(self, path: Path, info: ProjectInfo):
        self.path = path
        self.info = info

    @property
    def requirements_dir(self) -> Path:
        return self.path / "requirements"

    @property
    def plans_dir(self) -> Path:
        return self.path / "plans"

    @property
    def exports_dir(self) -> Path:
        return self.path / "exports"

    @property
    def slug(self) -> str:
        return self.info.slug


def create_project(client: str, project: str, org: str | None = None) -> Project:
    slug = f"{slugify(client)}/{slugify(project)}"
    path = workspace_root() / slug
    if (path / "project.json").exists():
        raise FileExistsError(f"project {slug} already exists at {path}")
    for sub in ("requirements", "plans", "exports"):
        (path / sub).mkdir(parents=True, exist_ok=True)
    info = ProjectInfo(client=client, project=project, slug=slug, org=org)
    (path / "project.json").write_text(info.model_dump_json(indent=2), encoding="utf-8")
    return Project(path, info)


def open_project(slug: str) -> Project:
    path = workspace_root() / slug
    meta = path / "project.json"
    if not meta.exists():
        known = ", ".join(p.slug for p in list_projects()) or "none"
        raise LookupError(f"unknown project {slug!r} (known: {known})")
    return Project(path, ProjectInfo.model_validate_json(meta.read_text(encoding="utf-8")))


def list_projects() -> list[Project]:
    try:
        root = workspace_root()
    except WorkspaceNotConfiguredError:
        return []
    out = []
    for meta in sorted(root.glob("*/*/project.json")):
        out.append(Project(meta.parent, ProjectInfo.model_validate_json(meta.read_text("utf-8"))))
    return out
