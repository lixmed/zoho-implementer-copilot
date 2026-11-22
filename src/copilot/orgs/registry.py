"""Org registry: one profile per connected Zoho org. Contains NO secrets.

Stored as JSON in the user data dir (override with COPILOT_HOME, e.g. for tests).
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import platformdirs
from pydantic import BaseModel, Field

Environment = Literal["production", "sandbox", "developer"]


class OrgProfile(BaseModel):
    slug: str
    name: str
    environment: Environment
    dc: str
    accounts_server: str
    api_domain: str
    scopes: list[str]
    zoho_org_id: str | None = None
    edition: str | None = None
    connected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RegistryFile(BaseModel):
    version: int = 1
    orgs: dict[str, OrgProfile] = Field(default_factory=dict)


class OrgNotFoundError(LookupError):
    pass


def home_dir() -> Path:
    override = os.environ.get("COPILOT_HOME")
    path = Path(override) if override else Path(platformdirs.user_data_dir("zoho-copilot", False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot make a slug from {name!r}")
    return slug


def environment_from_api_domain(api_domain: str) -> Environment:
    """Zoho uses sandbox.zohoapis.<tld> and developer.zohoapis.<tld> for non-production envs."""
    host = urlsplit(api_domain).hostname or ""
    if host.startswith("sandbox."):
        return "sandbox"
    if host.startswith("developer."):
        return "developer"
    return "production"


class OrgRegistry:
    def __init__(self, path: Path | None = None):
        self.path = path or home_dir() / "orgs.json"

    def _load(self) -> RegistryFile:
        if not self.path.exists():
            return RegistryFile()
        return RegistryFile.model_validate_json(self.path.read_text(encoding="utf-8"))

    def _save(self, data: RegistryFile) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(data.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def list(self) -> list[OrgProfile]:
        return list(self._load().orgs.values())

    def get(self, slug: str) -> OrgProfile:
        data = self._load()
        try:
            return data.orgs[slug]
        except KeyError:
            known = ", ".join(data.orgs) or "none"
            raise OrgNotFoundError(f"unknown org {slug!r} (connected: {known})") from None

    def upsert(self, profile: OrgProfile) -> None:
        data = self._load()
        data.orgs[profile.slug] = profile
        self._save(data)

    def remove(self, slug: str) -> None:
        data = self._load()
        if data.orgs.pop(slug, None) is None:
            raise OrgNotFoundError(f"unknown org {slug!r}")
        self._save(data)
