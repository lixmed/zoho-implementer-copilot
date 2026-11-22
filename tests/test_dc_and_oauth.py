from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

from copilot.auth import oauth
from copilot.auth.dc import UnknownDataCentreError, dc_for_accounts_server, get_dc


@pytest.mark.parametrize(
    ("server", "key"),
    [
        ("https://accounts.zoho.com", "us"),
        ("https://accounts.zoho.eu/", "eu"),
        ("https://accounts.zohocloud.ca", "ca"),
        ("https://accounts.zoho.uk", "uk"),
        ("https://accounts.zoho.sa", "sa"),
    ],
)
def test_known_accounts_servers(server, key):
    assert dc_for_accounts_server(server).key == key


@pytest.mark.parametrize(
    "server",
    [
        "http://accounts.zoho.com",  # not https
        "https://accounts.zoho.com.evil.io",
        "https://evil.io/?x=https://accounts.zoho.com",
        "https://user@accounts.zoho.com",
        "https://accounts.zoho.com/oauth",
    ],
)
def test_rejects_non_zoho_accounts_servers(server):
    with pytest.raises(UnknownDataCentreError):
        dc_for_accounts_server(server)


def test_get_dc_unknown():
    with pytest.raises(UnknownDataCentreError):
        get_dc("mars")


def test_authorize_url_has_offline_pkce_and_level1_scopes():
    url = oauth.build_authorize_url("cid", oauth.redirect_uri(), oauth.LEVEL1_SCOPES, "st", "ch")
    parts = urlsplit(url)
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert parts.netloc == "accounts.zoho.com"
    assert q["access_type"] == "offline"
    assert q["code_challenge_method"] == "S256"
    assert q["redirect_uri"] == "http://127.0.0.1:8765/callback"
    scopes = q["scope"].split(",")
    assert not any(s.startswith("ZohoCRM.modules") for s in scopes), "no record access in Level 1"


def test_parse_callback_ok():
    r = oauth.parse_callback(
        "code=abc&state=s1&location=eu&accounts-server=https%3A%2F%2Faccounts.zoho.eu", "s1"
    )
    assert (r.code, r.location, r.accounts_server) == ("abc", "eu", "https://accounts.zoho.eu")


@pytest.mark.parametrize(
    "query",
    [
        "code=abc&state=WRONG",
        "state=s1",
        "error=access_denied&state=s1",
        "code=abc&state=s1&accounts-server=https%3A%2F%2Fevil.io",
    ],
)
def test_parse_callback_rejects(query):
    with pytest.raises((oauth.OAuthError, UnknownDataCentreError)):
        oauth.parse_callback(query, "s1")


@respx.mock
def test_exchange_code_success_sends_verifier():
    route = respx.post("https://accounts.zoho.eu/oauth/v2/token").respond(
        200,
        json={
            "access_token": "at",
            "refresh_token": "rt",
            "api_domain": "https://www.zohoapis.eu",
            "expires_in": 3600,
        },
    )
    with httpx.Client() as http:
        tok = oauth.exchange_code(
            http, "https://accounts.zoho.eu", "cid", "sec", "code", "r", "ver"
        )
    assert tok.refresh_token == "rt" and tok.api_domain == "https://www.zohoapis.eu"
    sent = parse_qs(route.calls[0].request.content.decode())
    assert sent["code_verifier"] == ["ver"] and sent["grant_type"] == ["authorization_code"]
    assert "rt" not in repr(tok) and "at" not in repr(tok).replace("api_domain", "")


@respx.mock
def test_zoho_error_with_http_200_is_raised():
    respx.post("https://accounts.zoho.com/oauth/v2/token").respond(
        200, json={"error": "invalid_code"}
    )
    with httpx.Client() as http, pytest.raises(oauth.OAuthError, match="invalid_code"):
        oauth.exchange_code(http, "https://accounts.zoho.com", "cid", "sec", "bad", "r", "v")


def test_token_request_refuses_foreign_host():
    with httpx.Client() as http, pytest.raises(UnknownDataCentreError):
        oauth.refresh_access_token(http, "https://evil.io", "cid", "sec", "rt")


@respx.mock
def test_token_manager_caches_and_force_refreshes():
    route = respx.post("https://accounts.zoho.com/oauth/v2/token").respond(
        200,
        json={"access_token": "at", "api_domain": "https://www.zohoapis.com", "expires_in": 3600},
    )
    with httpx.Client() as http:
        tm = oauth.TokenManager(http, "https://accounts.zoho.com", "cid", "sec", "rt")
        assert tm.get() == "at"
        assert tm.get() == "at"
        assert route.call_count == 1
        tm.get(force_refresh=True)
        assert route.call_count == 2


@respx.mock
def test_revoke_uses_documented_endpoint():
    route = respx.post("https://accounts.zoho.com/oauth/v2/revoke/token").respond(200, json={})
    with httpx.Client() as http:
        oauth.revoke_refresh_token(http, "https://accounts.zoho.com", "cid", "sec", "rt")
    assert route.called
