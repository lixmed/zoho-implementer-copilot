"""Secret storage in the OS keychain (Windows Credential Manager / macOS Keychain / Secret Service).

Nothing in this module ever writes a secret to disk or logs it.

Keychain layout (service = SERVICE):
    "client"              -> JSON {"client_id": str, "secrets": {dc_key: client_secret}}
    "org:<slug>:refresh"  -> refresh token for one connected org

Zoho issues one client id, but a different client secret per data centre
(once multi-DC is enabled in the API console), hence the per-DC map.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field

import keyring
import keyring.errors

SERVICE = "zoho-implementer-copilot"
_CLIENT_KEY = "client"


class MissingSecretError(LookupError):
    pass


@dataclass
class ClientCredentials:
    client_id: str
    secrets: dict[str, str] = field(default_factory=dict)

    def secret_for(self, dc_key: str) -> str:
        try:
            return self.secrets[dc_key]
        except KeyError:
            raise MissingSecretError(
                f"no client secret stored for data centre {dc_key!r}. "
                f"Run: copilot client set --dc {dc_key}"
            ) from None

    def __repr__(self) -> str:  # never print secrets by accident
        return f"ClientCredentials(client_id={self.client_id!r}, dcs={sorted(self.secrets)})"


def save_client(creds: ClientCredentials) -> None:
    payload = json.dumps({"client_id": creds.client_id, "secrets": creds.secrets})
    keyring.set_password(SERVICE, _CLIENT_KEY, payload)


def load_client() -> ClientCredentials:
    raw = keyring.get_password(SERVICE, _CLIENT_KEY)
    if not raw:
        raise MissingSecretError("no Zoho API client configured. Run: copilot client set")
    data = json.loads(raw)
    return ClientCredentials(client_id=data["client_id"], secrets=dict(data.get("secrets", {})))


def _refresh_key(slug: str) -> str:
    return f"org:{slug}:refresh"


def save_refresh_token(slug: str, token: str) -> None:
    keyring.set_password(SERVICE, _refresh_key(slug), token)


def load_refresh_token(slug: str) -> str:
    token = keyring.get_password(SERVICE, _refresh_key(slug))
    if not token:
        raise MissingSecretError(
            f"no refresh token for org {slug!r}. Reconnect with: copilot connect"
        )
    return token


def delete_refresh_token(slug: str) -> None:
    with contextlib.suppress(keyring.errors.PasswordDeleteError):
        keyring.delete_password(SERVICE, _refresh_key(slug))
