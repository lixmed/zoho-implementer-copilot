"""End-to-end against a fake Zoho: connect (real callback server) -> snapshot -> MCP tools.

Only the network to Zoho is faked (respx). The CLI, OAuth callback server, keychain
layer, registry, read client and MCP server are the real code.
"""

import asyncio
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlencode, urlsplit

import fake_zoho
import keyring
import pytest
import respx

from copilot import cli
from copilot.auth import oauth, secrets
from copilot.mcp_server import server as mcp_mod
from copilot.orgs.registry import OrgRegistry

# RFC 7636 vector, so these tests don't depend on the PKCE exercise being done yet
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fake_browser(accounts_server=fake_zoho.ACCOUNTS, tamper_state=False):
    """Acts like the user approving in the browser: Zoho redirects to our callback."""

    def open_url(url: str) -> bool:
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        assert q["code_challenge"] == CHALLENGE
        params = {
            "code": "fake-code",
            "state": "evil" if tamper_state else q["state"],
            "location": "us",
            "accounts-server": accounts_server,
        }
        target = f"{q['redirect_uri']}?{urlencode(params)}"

        def hit():
            for _ in range(50):
                try:
                    urllib.request.urlopen(target, timeout=2)  # noqa: S310 (localhost)
                    return
                except urllib.error.HTTPError:
                    return  # server answered (e.g. 400 on rejected callback)
                except OSError:
                    time.sleep(0.1)

        threading.Thread(target=hit, daemon=True).start()
        return True

    return open_url


@pytest.fixture
def setup(monkeypatch):
    secrets.save_client(secrets.ClientCredentials("fake-client-id", {"us": "fake-secret"}))
    monkeypatch.setattr(oauth, "new_pkce_pair", lambda: (VERIFIER, CHALLENGE))
    mcp_mod._clients.clear()


def connect(monkeypatch, browser, env="developer"):
    monkeypatch.setattr(cli.webbrowser, "open", browser)
    return cli.main(["connect", "--name", "Demo Dev Org", "--env", env, "--port", str(free_port())])


@respx.mock
def test_full_flow_connect_snapshot_mcp(setup, monkeypatch, tmp_path, capsys):
    token = respx.post(f"{fake_zoho.ACCOUNTS}/oauth/v2/token")
    token.side_effect = lambda req: respx.MockResponse(200, json=fake_zoho.token_body())
    fake_zoho.mount(respx.mock)

    # 1. connect
    assert connect(monkeypatch, fake_browser()) == 0
    sent = parse_qs(token.calls[0].request.content.decode())
    assert sent["code_verifier"] == [VERIFIER]
    assert sent["code"] == ["fake-code"]

    org = OrgRegistry().get("demo-dev-org")
    assert (org.environment, org.dc, org.api_domain) == ("developer", "us", fake_zoho.DEV_API)
    assert org.zoho_org_id == "900001"
    assert keyring.get_password(secrets.SERVICE, "org:demo-dev-org:refresh") == "fake-refresh"
    registry_text = OrgRegistry().path.read_text()
    assert "fake-refresh" not in registry_text and "fake-secret" not in registry_text

    # 2. snapshot (skips modules that are not api_supported)
    assert cli.main(["snapshot", "demo-dev-org", "--out", str(tmp_path)]) == 0
    snap = json.loads((tmp_path / "demo-dev-org.json").read_text())
    assert set(snap["fields"]) == {"Leads", "Deals", "Properties"}
    assert snap["fields"]["Deals"][1]["lookup_module"] == "Properties"
    snap_text = json.dumps(snap)
    for pii in ("demo-realty.test", "Jane Fake", "555"):
        assert pii not in snap_text

    # 3. MCP tools through the real server object
    def call(name, **args):
        result = asyncio.run(mcp_mod.server.call_tool(name, args))
        assert not result.is_error, result
        if result.structured_content is not None:
            return json.dumps(result.structured_content)
        return " ".join(getattr(c, "text", "") for c in result.content)

    assert "demo-dev-org" in call("list_orgs")
    assert "Budget_Range" in call("get_fields", org="demo-dev-org", module="Leads")
    assert "Lead Information" in call("get_layouts", org="demo-dev-org", module="Leads")
    assert "assign_agent" in call("get_functions", org="demo-dev-org")
    org_info = call("get_org_info", org="demo-dev-org")
    assert "Demo Realty" in org_info and "owner@" not in org_info


@respx.mock
def test_connect_refuses_env_mismatch_and_revokes(setup, monkeypatch, capsys):
    respx.post(f"{fake_zoho.ACCOUNTS}/oauth/v2/token").respond(
        200,
        json=fake_zoho.token_body(api_domain="https://www.zohoapis.com"),  # production!
    )
    revoke = respx.post(f"{fake_zoho.ACCOUNTS}/oauth/v2/revoke/token").respond(200)
    assert connect(monkeypatch, fake_browser()) == 2
    assert revoke.called
    assert OrgRegistry().list() == []
    assert "Refusing" in capsys.readouterr().err


@respx.mock
def test_connect_rejects_tampered_state(setup, monkeypatch, capsys):
    token = respx.post(f"{fake_zoho.ACCOUNTS}/oauth/v2/token").respond(200, json={})
    assert connect(monkeypatch, fake_browser(tamper_state=True)) == 1
    assert not token.called
    assert "state mismatch" in capsys.readouterr().err


@respx.mock
def test_connect_rejects_foreign_accounts_server(setup, monkeypatch, capsys):
    token = respx.post(url__regex=r".*/oauth/v2/token").respond(200, json={})
    assert connect(monkeypatch, fake_browser(accounts_server="https://evil.example")) == 1
    assert not token.called  # the client secret never left the machine
    assert OrgRegistry().list() == []
