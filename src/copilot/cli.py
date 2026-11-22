"""Command line: `copilot <command>`."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import webbrowser
from pathlib import Path

import httpx

from copilot.auth import oauth, secrets
from copilot.auth.dc import DATA_CENTRES, dc_for_accounts_server, get_dc
from copilot.orgs.registry import OrgProfile, OrgRegistry, environment_from_api_domain, slugify
from copilot.orgs.session import open_read_client
from copilot.zoho import metadata
from copilot.zoho.client import ZohoReadClient


def cmd_client_set(args: argparse.Namespace) -> int:
    try:
        creds = secrets.load_client()
    except secrets.MissingSecretError:
        creds = None
    client_id = input(f"Zoho client ID{f' [{creds.client_id}]' if creds else ''}: ").strip()
    if not client_id and creds:
        client_id = creds.client_id
    if not client_id:
        print("client ID is required", file=sys.stderr)
        return 1
    stored = dict(creds.secrets) if creds and creds.client_id == client_id else {}
    for key in args.dc:
        dc = get_dc(key)
        secret = getpass.getpass(f"Client secret for {dc.label} ({dc.key}), input hidden: ").strip()
        if secret:
            stored[dc.key] = secret
    secrets.save_client(secrets.ClientCredentials(client_id=client_id, secrets=stored))
    print(
        f"Saved to OS keychain. Data centres with a secret: {', '.join(sorted(stored)) or 'none'}"
    )
    return 0


def cmd_connect(args: argparse.Namespace) -> int:
    if args.env == "production":
        print(
            "Production orgs are blocked until Phase 7 (production safeguards). "
            "Use a developer or sandbox org.",
            file=sys.stderr,
        )
        return 2
    slug = slugify(args.slug or args.name)
    creds = secrets.load_client()
    redirect = oauth.redirect_uri(args.port)
    state = oauth.new_state()
    verifier, challenge = oauth.new_pkce_pair()
    url = oauth.build_authorize_url(
        creds.client_id, redirect, oauth.LEVEL1_SCOPES, state, challenge
    )

    print(f"Redirect URI (must be registered in api-console.zoho.com): {redirect}")
    print("Opening Zoho consent page. If it doesn't open, paste this URL into a browser:\n")
    print(url, "\n")
    webbrowser.open(url)
    callback = oauth.wait_for_callback(args.port, state)

    dc = dc_for_accounts_server(callback.accounts_server)
    with httpx.Client(timeout=30.0) as http:
        tok = oauth.exchange_code(
            http,
            callback.accounts_server,
            creds.client_id,
            creds.secret_for(dc.key),
            callback.code,
            redirect,
            verifier,
        )
        if not tok.refresh_token:
            print(
                "Zoho did not return a refresh token; cannot keep the org connected.",
                file=sys.stderr,
            )
            return 1

        detected = environment_from_api_domain(tok.api_domain)
        if detected != args.env:
            print(
                f"Refusing: you said --env {args.env} but Zoho issued a token for a {detected} "
                f"environment ({tok.api_domain}). Nothing was saved.",
                file=sys.stderr,
            )
            oauth.revoke_refresh_token(
                http,
                callback.accounts_server,
                creds.client_id,
                creds.secret_for(dc.key),
                tok.refresh_token,
            )
            return 2

        class _Static:
            def get(self, force_refresh: bool = False) -> str:
                return tok.access_token

        org_info = metadata.get_org(ZohoReadClient(tok.api_domain, _Static(), http=http))

    secrets.save_refresh_token(slug, tok.refresh_token)
    profile = OrgProfile(
        slug=slug,
        name=args.name,
        environment=detected,
        dc=dc.key,
        accounts_server=callback.accounts_server,
        api_domain=tok.api_domain,
        scopes=(tok.scope.split() if tok.scope else list(oauth.LEVEL1_SCOPES)),
        zoho_org_id=str(org_info.get("id")) if org_info.get("id") else None,
        edition=org_info.get("type"),
    )
    OrgRegistry().upsert(profile)
    print(f"Connected '{args.name}' as '{slug}' ({detected}, DC {dc.key}, {tok.api_domain}).")
    return 0


def cmd_orgs_list(args: argparse.Namespace) -> int:
    orgs = OrgRegistry().list()
    if not orgs:
        print("No orgs connected. Run: copilot connect --name 'My Dev Org' --env developer")
    for o in orgs:
        print(f"{o.slug:24} {o.environment:11} {o.dc:3} {o.name}")
    return 0


def cmd_orgs_remove(args: argparse.Namespace) -> int:
    registry = OrgRegistry()
    org = registry.get(args.slug)
    if args.revoke:
        creds = secrets.load_client()
        with httpx.Client(timeout=30.0) as http:
            oauth.revoke_refresh_token(
                http,
                org.accounts_server,
                creds.client_id,
                creds.secret_for(org.dc),
                secrets.load_refresh_token(org.slug),
            )
        print("Refresh token revoked at Zoho.")
    secrets.delete_refresh_token(org.slug)
    registry.remove(org.slug)
    print(f"Removed {org.slug} from this machine.")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    org, c = open_read_client(args.slug)
    modules = [m for m in metadata.list_modules(c) if m.get("api_supported")]
    snap = {
        "org": org.model_dump(mode="json", include={"slug", "name", "environment", "dc"}),
        "modules": modules,
        "fields": {m["api_name"]: metadata.get_fields(c, m["api_name"]) for m in modules},
        "workflow_rules": metadata.list_workflow_rules(c),
        "functions": metadata.list_functions(c),
    }
    out = Path(args.out) / f"{org.slug}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    print(
        f"Wrote {out} ({c.calls_made} API calls, credits remaining: {c.credits_remaining or 'n/a'})"
    )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from copilot.mcp_server.server import run

    run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="copilot", description="Zoho Implementer Copilot")
    sub = p.add_subparsers(dest="cmd", required=True)

    client = sub.add_parser("client", help="manage the Zoho API client credentials")
    client_sub = client.add_subparsers(dest="client_cmd", required=True)
    cs = client_sub.add_parser("set", help="store client id/secret in the OS keychain")
    cs.add_argument("--dc", nargs="+", default=["us"], choices=sorted(DATA_CENTRES))
    cs.set_defaults(func=cmd_client_set)

    c = sub.add_parser("connect", help="connect a Zoho org via OAuth")
    c.add_argument("--name", required=True, help="display name, e.g. 'My Dev Org'")
    c.add_argument("--slug", help="short id (default: derived from name)")
    c.add_argument("--env", required=True, choices=["developer", "sandbox", "production"])
    c.add_argument("--port", type=int, default=oauth.DEFAULT_PORT)
    c.set_defaults(func=cmd_connect)

    orgs = sub.add_parser("orgs", help="list or remove connected orgs")
    orgs_sub = orgs.add_subparsers(dest="orgs_cmd", required=True)
    orgs_sub.add_parser("list").set_defaults(func=cmd_orgs_list)
    rm = orgs_sub.add_parser("remove")
    rm.add_argument("slug")
    rm.add_argument("--revoke", action="store_true", help="also revoke the token at Zoho")
    rm.set_defaults(func=cmd_orgs_remove)

    s = sub.add_parser("snapshot", help="dump org metadata to JSON (no records)")
    s.add_argument("slug")
    s.add_argument("--out", default="snapshots")
    s.set_defaults(func=cmd_snapshot)

    sub.add_parser("serve", help="run the MCP server (stdio)").set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (secrets.MissingSecretError, oauth.OAuthError, LookupError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
