"""Read-only Zoho CRM API client.

This class deliberately exposes only GET. The read tier is enforced structurally:
there is no code path that can write to Zoho from Phase 1. Write methods will live in a
separate class (Phase 4) that requires an approved change set.
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Any, Protocol

import httpx

API_VERSION = "v8"
log = logging.getLogger(__name__)


class TokenSource(Protocol):
    def get(self, force_refresh: bool = False) -> str: ...


class ZohoAPIError(RuntimeError):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(f"Zoho API {status} {code}: {message}")
        self.status, self.code, self.message, self.details = status, code, message, details


class RateLimitedError(ZohoAPIError):
    pass


class ZohoReadClient:
    def __init__(
        self,
        api_domain: str,
        tokens: TokenSource,
        http: httpx.Client | None = None,
        cache_ttl: float = 300.0,
    ):
        self.base = f"{api_domain.rstrip('/')}/crm/{API_VERSION}"
        self._tokens = tokens
        self._http = http or httpx.Client(timeout=30.0)
        self._ttl = cache_ttl
        self._cache: dict[tuple[str, tuple], tuple[float, dict]] = {}
        self.credits_remaining: int | None = None
        self.calls_made = 0

    def get(self, path: str, params: dict[str, Any] | None = None, use_cache: bool = True) -> dict:
        key = (path, tuple(sorted((params or {}).items())))
        if use_cache and key in self._cache:
            stored_at, body = self._cache[key]
            if time.monotonic() - stored_at < self._ttl:
                return body
        body = self._request(path, params, retry_auth=True)
        self._cache[key] = (time.monotonic(), body)
        return body

    def clear_cache(self) -> None:
        self._cache.clear()

    def _request(self, path: str, params: dict | None, retry_auth: bool) -> dict:
        token = self._tokens.get()
        resp = self._http.get(
            f"{self.base}{path}",
            params=params,
            headers={"Authorization": f"Zoho-oauthtoken {token}"},
        )
        self.calls_made += 1
        if (remaining := resp.headers.get("X-API-CREDITS-REMAINING")) is not None:
            with contextlib.suppress(ValueError):
                self.credits_remaining = int(remaining)

        if resp.status_code == 204:  # Zoho returns 204 when a list is empty
            return {}
        if resp.status_code == 200:
            return resp.json()

        code, message, details = _parse_error(resp)
        if resp.status_code == 401 and code == "INVALID_TOKEN" and retry_auth:
            self._tokens.get(force_refresh=True)
            return self._request(path, params, retry_auth=False)
        if resp.status_code == 429 or code == "TOO_MANY_REQUESTS":
            raise RateLimitedError(resp.status_code, code, message, details)
        raise ZohoAPIError(resp.status_code, code, message, details)


def _parse_error(resp: httpx.Response) -> tuple[str, str, Any]:
    try:
        body = resp.json()
    except ValueError:
        return "HTTP_ERROR", resp.text[:200], None
    # Error bodies come either flat {code,message} or wrapped {data:[{code,...}]}
    if isinstance(body.get("data"), list) and body["data"]:
        body = body["data"][0]
    return body.get("code", "UNKNOWN"), body.get("message", ""), body.get("details")
