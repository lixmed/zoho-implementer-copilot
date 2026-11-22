"""Zoho data centres.

Accounts servers come from Zoho's docs (accounts multi-dc page) and the live
https://accounts.zoho.com/oauth/serverinfo list (checked 2026-11-22).

API base URLs are intentionally NOT hardcoded: Zoho returns ``api_domain`` in the
token response, and we store that per org. The docs only confirm a few API domains,
so guessing the rest would be inventing.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class DataCentre:
    key: str
    label: str
    accounts_server: str


DATA_CENTRES: dict[str, DataCentre] = {
    dc.key: dc
    for dc in (
        DataCentre("us", "United States", "https://accounts.zoho.com"),
        DataCentre("eu", "Europe", "https://accounts.zoho.eu"),
        DataCentre("in", "India", "https://accounts.zoho.in"),
        DataCentre("au", "Australia", "https://accounts.zoho.com.au"),
        DataCentre("jp", "Japan", "https://accounts.zoho.jp"),
        DataCentre("ca", "Canada", "https://accounts.zohocloud.ca"),
        DataCentre("sa", "Saudi Arabia", "https://accounts.zoho.sa"),
        DataCentre("uk", "United Kingdom", "https://accounts.zoho.uk"),
        DataCentre("ae", "United Arab Emirates", "https://accounts.zoho.ae"),
        DataCentre("sg", "Singapore", "https://accounts.zoho.sg"),
        # China is listed in the CRM multi-dc doc but not in the live serverinfo list.
        DataCentre("cn", "China", "https://accounts.zoho.com.cn"),
    )
}

# Where the authorization request starts. Zoho redirects back with
# `location` and `accounts-server` telling us the user's real DC.
DEFAULT_AUTH_START = DATA_CENTRES["us"].accounts_server


class UnknownDataCentreError(ValueError):
    pass


def dc_for_accounts_server(accounts_server: str) -> DataCentre:
    """Map an `accounts-server` value from the OAuth callback to a known DC.

    This is a security check, not just a lookup: we only ever send the client
    secret and auth code to a Zoho accounts server on this allowlist. A tampered
    callback pointing somewhere else is rejected.
    """
    parts = urlsplit(accounts_server.strip())
    if parts.scheme != "https" or parts.path not in ("", "/") or parts.query or parts.username:
        raise UnknownDataCentreError(f"not a valid accounts server: {accounts_server!r}")
    normalized = f"https://{parts.hostname}"
    for dc in DATA_CENTRES.values():
        if dc.accounts_server == normalized:
            return dc
    raise UnknownDataCentreError(f"accounts server not on the Zoho allowlist: {accounts_server!r}")


def get_dc(key: str) -> DataCentre:
    try:
        return DATA_CENTRES[key.lower()]
    except KeyError:
        raise UnknownDataCentreError(
            f"unknown data centre {key!r}; expected one of {', '.join(DATA_CENTRES)}"
        ) from None
