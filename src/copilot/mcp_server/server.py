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


def run() -> None:
    logging.basicConfig(level=logging.INFO)  # basicConfig logs to stderr; stdout belongs to MCP
    server.run("stdio")
