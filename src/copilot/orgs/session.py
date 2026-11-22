"""Builds a ready-to-use read client for a connected org."""

from __future__ import annotations

import httpx

from copilot.auth import secrets
from copilot.auth.oauth import TokenManager
from copilot.orgs.registry import OrgProfile, OrgRegistry
from copilot.zoho.client import ZohoReadClient


def open_read_client(
    slug: str, registry: OrgRegistry | None = None, http: httpx.Client | None = None
) -> tuple[OrgProfile, ZohoReadClient]:
    registry = registry or OrgRegistry()
    org = registry.get(slug)
    creds = secrets.load_client()
    http = http or httpx.Client(timeout=30.0)
    tokens = TokenManager(
        http=http,
        accounts_server=org.accounts_server,
        client_id=creds.client_id,
        client_secret=creds.secret_for(org.dc),
        refresh_token=secrets.load_refresh_token(slug),
    )
    return org, ZohoReadClient(org.api_domain, tokens, http=http)
