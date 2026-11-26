"""Zoho OAuth 2.0: server-based client, PKCE, localhost redirect.

Flow (docs: zoho.com/accounts/protocol/oauth/web-server-applications.html):
  1. Open  {accounts.zoho.com}/oauth/v2/auth?...&access_type=offline&code_challenge=...
  2. Zoho redirects to our localhost callback with code, state, location, accounts-server
  3. Validate state and that accounts-server is a real Zoho DC (allowlist)
  4. POST {accounts-server}/oauth/v2/token  -> access_token, refresh_token, api_domain
Refresh tokens never expire until revoked, so they go straight to the OS keychain.
Access tokens (1 hour) are kept in memory only.
"""

from __future__ import annotations

import http.server
import re
import secrets as pysecrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from copilot.auth import pkce
from copilot.auth.dc import DEFAULT_AUTH_START, UnknownDataCentreError, dc_for_accounts_server

# Level 1 (Planner) scopes: metadata only. No ZohoCRM.modules.* => no customer records.
LEVEL1_SCOPES: tuple[str, ...] = (
    "ZohoCRM.org.READ",
    "ZohoCRM.settings.modules.READ",
    "ZohoCRM.settings.fields.READ",
    "ZohoCRM.settings.layouts.READ",
    "ZohoCRM.settings.related_lists.READ",
    "ZohoCRM.settings.workflow_rules.READ",
    "ZohoCRM.settings.blueprint.READ",
    "ZohoCRM.settings.functions.READ",
    "ZohoCRM.settings.roles.READ",
    "ZohoCRM.settings.profiles.READ",
    "ZohoCRM.users.READ",
)

DEFAULT_PORT = 8765
CALLBACK_PATH = "/callback"


class OAuthError(RuntimeError):
    pass


def redirect_uri(port: int = DEFAULT_PORT) -> str:
    return f"http://127.0.0.1:{port}{CALLBACK_PATH}"


def build_authorize_url(
    client_id: str, redirect: str, scopes: tuple[str, ...], state: str, code_challenge: str
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "scope": ",".join(scopes),
        "redirect_uri": redirect,
        "access_type": "offline",  # required to receive a refresh token
        "prompt": "consent",  # always show consent so a refresh token is issued again on reconnect
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{DEFAULT_AUTH_START}/oauth/v2/auth?{urlencode(params)}"


@dataclass(frozen=True)
class CallbackResult:
    code: str
    location: str | None
    accounts_server: str


def parse_callback(query: str, expected_state: str) -> CallbackResult:
    """Validate the redirect query string. Raises OAuthError on anything suspicious."""
    q = {k: v[0] for k, v in parse_qs(query).items()}
    if "error" in q:
        raise OAuthError(f"Zoho returned an error: {q['error']}")
    if not pysecrets.compare_digest(q.get("state", ""), expected_state):
        raise OAuthError("state mismatch, possible CSRF; aborting")
    code = q.get("code")
    if not code:
        raise OAuthError("callback had no authorization code")
    accounts_server = q.get("accounts-server", DEFAULT_AUTH_START)
    try:
        dc_for_accounts_server(accounts_server)
    except UnknownDataCentreError as exc:
        raise OAuthError(str(exc)) from None
    return CallbackResult(code=code, location=q.get("location"), accounts_server=accounts_server)


_PAGE = (
    "<html><body style='font-family:sans-serif'><h3>{msg}</h3>"
    "<p>You can close this tab and go back to the terminal.</p></body></html>"
)


def wait_for_callback(port: int, expected_state: str, timeout: float = 180.0) -> CallbackResult:
    """Serve exactly one request on 127.0.0.1:<port>/callback and return the parsed result."""
    outcome: dict[str, object] = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (stdlib name)
            parts = urlsplit(self.path)
            if parts.path != CALLBACK_PATH:
                self.send_response(404)
                self.end_headers()
                return
            try:
                outcome["result"] = parse_callback(parts.query, expected_state)
                msg, status = "Connected to Zoho.", 200
            except OAuthError as exc:
                outcome["error"] = exc
                msg, status = f"Connection failed: {exc}", 400
            body = _PAGE.format(msg=msg).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            done.set()

        def log_message(self, *args):  # keep the auth code out of the console
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 0.5
    deadline = time.monotonic() + timeout
    try:
        while not done.is_set():
            if time.monotonic() > deadline:
                raise OAuthError("timed out waiting for the Zoho redirect")
            server.handle_request()
    finally:
        server.server_close()
    if "error" in outcome:
        raise outcome["error"]  # type: ignore[misc]
    return outcome["result"]  # type: ignore[return-value]


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    expires_in: int
    api_domain: str
    refresh_token: str | None = None
    scope: str | None = None

    def __repr__(self) -> str:
        return f"TokenResponse(api_domain={self.api_domain!r}, expires_in={self.expires_in})"


# Zoho's docs disagree on how to send token parameters: the Accounts reference shows them as
# query parameters on the POST, the CRM docs say "form-data" in the body. In practice Zoho
# sometimes answers a format it dislikes with a generic HTML error page (not JSON), and that
# request is not processed, so the next format can still use the same grant code.
TOKEN_TRANSPORTS = ("form", "query", "multipart")
last_transport: str | None = None  # which one Zoho accepted most recently (for diagnostics)


def _post_token(http: httpx.Client, url: str, data: dict[str, str], transport: str):
    if transport == "query":
        return http.post(url, params=data)
    if transport == "multipart":
        return http.post(url, files={k: (None, v) for k, v in data.items()})
    return http.post(url, data=data)


def _html_excerpt(text: str) -> str:
    page = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    return " ".join(re.sub(r"<[^>]+>", " ", page).split())[:300]


def _token_request(http: httpx.Client, accounts_server: str, data: dict[str, str]) -> TokenResponse:
    global last_transport
    dc_for_accounts_server(accounts_server)  # never send secrets to a non-Zoho host
    url = f"{accounts_server}/oauth/v2/token"
    failures = []
    for transport in TOKEN_TRANSPORTS:
        resp = _post_token(http, url, data, transport)
        try:
            body = resp.json()
            last_transport = transport
            break
        except ValueError:
            failures.append(f"{transport}: HTTP {resp.status_code} {_html_excerpt(resp.text)!r}")
    else:
        raise OAuthError("token endpoint rejected every request format: " + " | ".join(failures))
    # Zoho reports some token errors with HTTP 200 and an "error" key.
    if resp.status_code != 200 or "error" in body:
        raise OAuthError(f"token request failed: {body.get('error', resp.status_code)}")
    return TokenResponse(
        access_token=body["access_token"],
        expires_in=int(body.get("expires_in", 3600)),
        api_domain=body["api_domain"],
        refresh_token=body.get("refresh_token"),
        scope=body.get("scope"),
    )


def exchange_code(
    http: httpx.Client,
    accounts_server: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect: str,
    code_verifier: str,
) -> TokenResponse:
    return _token_request(
        http,
        accounts_server,
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect,
            "code": code,
            "code_verifier": code_verifier,
        },
    )


def refresh_access_token(
    http: httpx.Client, accounts_server: str, client_id: str, client_secret: str, refresh_token: str
) -> TokenResponse:
    return _token_request(
        http,
        accounts_server,
        {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
    )


def revoke_refresh_token(
    http: httpx.Client, accounts_server: str, client_id: str, client_secret: str, token: str
) -> None:
    """POST {accounts}/oauth/v2/revoke/token.

    Docs: zoho.com/accounts/protocol/oauth/revoke-refresh-token.html
    Revoking the refresh token also invalidates the access tokens derived from it.
    """
    dc_for_accounts_server(accounts_server)
    resp = http.post(
        f"{accounts_server}/oauth/v2/revoke/token",
        data={"token": token, "token_type": "refresh_token"},
        auth=(client_id, client_secret),
    )
    if resp.status_code >= 400:
        raise OAuthError(f"revoke failed with HTTP {resp.status_code}")


def new_state() -> str:
    return pysecrets.token_urlsafe(32)


def new_pkce_pair() -> tuple[str, str]:
    # base64url alphabet only (A-Z a-z 0-9 - _): a valid RFC 7636 subset that avoids "." and "~",
    # which Zoho's token endpoint appeared to reject intermittently with an HTML error page.
    verifier = pysecrets.token_urlsafe(48)  # 64 chars
    return verifier, pkce.challenge_s256(verifier)


class TokenManager:
    """Hands out a valid access token for one org, refreshing it when it's close to expiry.

    Access tokens live only in memory. Zoho allows max 10 refreshes per refresh
    token per 10 minutes, so we cache aggressively and refresh 2 minutes early.
    """

    EARLY_REFRESH_SECONDS = 120

    def __init__(
        self,
        http: httpx.Client,
        accounts_server: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ):
        self._http = http
        self._accounts_server = accounts_server
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._access: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def get(self, force_refresh: bool = False) -> str:
        with self._lock:
            if force_refresh or not self._access or time.monotonic() >= self._expires_at:
                tok = refresh_access_token(
                    self._http,
                    self._accounts_server,
                    self._client_id,
                    self._client_secret,
                    self._refresh_token,
                )
                self._access = tok.access_token
                self._expires_at = time.monotonic() + tok.expires_in - self.EARLY_REFRESH_SECONDS
            return self._access
