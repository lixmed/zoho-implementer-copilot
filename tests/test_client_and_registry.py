import httpx
import pytest
import respx

from copilot.auth import secrets
from copilot.orgs.registry import (
    OrgNotFoundError,
    OrgProfile,
    OrgRegistry,
    environment_from_api_domain,
    slugify,
)
from copilot.zoho import metadata
from copilot.zoho.client import RateLimitedError, ZohoAPIError, ZohoReadClient

API = "https://www.zohoapis.com/crm/v8"


class FakeTokens:
    def __init__(self):
        self.refreshes = 0

    def get(self, force_refresh=False):
        if force_refresh:
            self.refreshes += 1
        return f"token{self.refreshes}"


def client(tokens=None):
    return ZohoReadClient("https://www.zohoapis.com", tokens or FakeTokens(), http=httpx.Client())


def test_client_has_no_write_methods():
    for name in ("post", "put", "patch", "delete"):
        assert not hasattr(ZohoReadClient, name)


@respx.mock
def test_get_caches_and_tracks_credits():
    route = respx.get(f"{API}/settings/modules").respond(
        200, json={"modules": []}, headers={"X-API-CREDITS-REMAINING": "4200"}
    )
    c = client()
    c.get("/settings/modules")
    c.get("/settings/modules")
    assert route.call_count == 1
    assert c.credits_remaining == 4200


@respx.mock
def test_204_means_empty():
    respx.get(f"{API}/settings/blueprints").respond(204)
    assert client().get("/settings/blueprints") == {}


@respx.mock
def test_invalid_token_refreshes_once_then_retries():
    route = respx.get(f"{API}/settings/roles")
    route.side_effect = [
        httpx.Response(401, json={"code": "INVALID_TOKEN", "message": "invalid oauth token"}),
        httpx.Response(200, json={"roles": []}),
    ]
    tokens = FakeTokens()
    assert client(tokens).get("/settings/roles") == {"roles": []}
    assert tokens.refreshes == 1
    assert route.calls[1].request.headers["Authorization"] == "Zoho-oauthtoken token1"


@respx.mock
def test_rate_limit_and_scope_errors():
    respx.get(f"{API}/settings/fields").respond(
        429, json={"code": "TOO_MANY_REQUESTS", "message": "slow down"}
    )
    with pytest.raises(RateLimitedError):
        client().get("/settings/fields", {"module": "Leads"})
    respx.get(f"{API}/users").respond(
        401, json={"code": "OAUTH_SCOPE_MISMATCH", "message": "scope"}
    )
    with pytest.raises(ZohoAPIError, match="OAUTH_SCOPE_MISMATCH"):
        client().get("/users")


@respx.mock
def test_metadata_trims_and_masks():
    respx.get(f"{API}/settings/fields").respond(
        200,
        json={
            "fields": [
                {
                    "api_name": "Lead_Status",
                    "field_label": "Lead Status",
                    "data_type": "picklist",
                    "custom_field": False,
                    "created_source": "default",
                    "pick_list_values": [{"display_value": "Hot", "actual_value": "Hot", "x": 1}],
                }
            ]
        },
    )
    fields = metadata.get_fields(client(), "Leads")
    assert fields == [
        {
            "api_name": "Lead_Status",
            "field_label": "Lead Status",
            "data_type": "picklist",
            "custom_field": False,
            "pick_list_values": [{"display_value": "Hot", "actual_value": "Hot"}],
        }
    ]

    respx.get(f"{API}/org").respond(
        200, json={"org": [{"id": "1", "company_name": "Fake Co", "primary_email": "a@b.c"}]}
    )
    assert metadata.get_org(client()) == {"id": "1", "company_name": "Fake Co"}


def test_environment_detection():
    assert environment_from_api_domain("https://sandbox.zohoapis.eu") == "sandbox"
    assert environment_from_api_domain("https://developer.zohoapis.com") == "developer"
    assert environment_from_api_domain("https://www.zohoapis.in") == "production"


def test_registry_roundtrip_and_no_secrets_on_disk(tmp_path):
    reg = OrgRegistry(tmp_path / "orgs.json")
    profile = OrgProfile(
        slug=slugify("My Dev Org!"),
        name="My Dev Org!",
        environment="developer",
        dc="us",
        accounts_server="https://accounts.zoho.com",
        api_domain="https://developer.zohoapis.com",
        scopes=["ZohoCRM.settings.modules.READ"],
    )
    reg.upsert(profile)
    secrets.save_refresh_token(profile.slug, "super-secret-refresh")
    assert reg.get("my-dev-org").name == "My Dev Org!"
    assert "super-secret-refresh" not in (tmp_path / "orgs.json").read_text()
    reg.remove("my-dev-org")
    with pytest.raises(OrgNotFoundError):
        reg.get("my-dev-org")


def test_client_credentials_repr_hides_secrets():
    creds = secrets.ClientCredentials("cid", {"us": "s3cr3t"})
    secrets.save_client(creds)
    loaded = secrets.load_client()
    assert loaded.secret_for("us") == "s3cr3t"
    assert "s3cr3t" not in repr(loaded)
    with pytest.raises(secrets.MissingSecretError):
        loaded.secret_for("eu")
