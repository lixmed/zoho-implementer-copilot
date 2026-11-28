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
    scopes = oauth.LEVEL1_SCOPES
    if args.allow_writes:
        from copilot.zoho.write import BUILDER_WRITE_SCOPES, FUNCTION_WRITE_SCOPES

        scopes = scopes + FUNCTION_WRITE_SCOPES + BUILDER_WRITE_SCOPES
        print(
            "Requesting write scopes: Deluge functions, modules, fields, layouts, "
            "workflow rules and their actions (no record access)."
        )
    if args.allow_test_records:
        from copilot.zoho.write import FUNCTION_EXECUTE_SCOPES, test_record_scopes

        modules = [m.strip() for m in (args.test_modules or "").split(",") if m.strip()]
        if not modules:
            print(
                "--allow-test-records needs --test-modules, e.g. --test-modules Leads,Deals",
                file=sys.stderr,
            )
            return 2
        scopes = scopes + test_record_scopes(modules)
        if not args.no_execute_scope:
            scopes = scopes + FUNCTION_EXECUTE_SCOPES
        print(
            f"Requesting test-record access for {modules} (fake test records only; sandbox/test orgs)."
        )
    url = oauth.build_authorize_url(creds.client_id, redirect, scopes, state, challenge)

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
        print(f"Token exchange OK (request format: {oauth.last_transport}).")
        if not tok.refresh_token:
            print(
                "Zoho did not return a refresh token; cannot keep the org connected.",
                file=sys.stderr,
            )
            return 1

        class _Static:
            def get(self, force_refresh: bool = False) -> str:
                return tok.access_token

        reader = ZohoReadClient(tok.api_domain, _Static(), http=http)
        detected = environment_from_api_domain(tok.api_domain)

        def refuse(message: str, show_edition: bool = True) -> int:
            print(f"Refusing: {message} Nothing was saved.", file=sys.stderr)
            if show_edition:
                try:  # evidence for the user: how does Zoho describe this org's edition?
                    print("Org edition info (no names/emails):", file=sys.stderr)
                    print(json.dumps(metadata.org_edition_info(reader), indent=2), file=sys.stderr)
                except Exception as exc:  # noqa: BLE001 (diagnostic only, must not block revoke)
                    print(f"(could not read org edition info: {exc})", file=sys.stderr)
            oauth.revoke_refresh_token(
                http,
                callback.accounts_server,
                creds.client_id,
                creds.secret_for(dc.key),
                tok.refresh_token,
            )
            return 2

        environment = detected
        if args.env == "test":
            # Your own free/trial org: Zoho reports it as "production". Accept it only if it's
            # unpaid AND the user confirms its id. A real client's live org is almost always paid.
            if detected != "production":
                return refuse(
                    f"--env test is for a production-type org you own, but Zoho says this is {detected}; use --env {detected}."
                )
            lic = metadata.org_edition_info(reader).get("license_details", {})
            if lic.get("paid") is not False:
                return refuse(
                    "this org has a PAID license, so it looks like a real (client) org. --env test only accepts unpaid orgs."
                )
            org_info = metadata.get_org(reader)
            org_id = str(org_info.get("id", ""))
            print(
                f"\nOrg: {org_info.get('company_name', '?')} (id {org_id}): "
                f"{lic.get('paid_type')}, {lic.get('trial_type')} trial until {lic.get('trial_expiry')}"
            )
            typed = input(
                "Type the org id to confirm this is YOUR OWN test org with fake data only: "
            )
            if not org_id or typed.strip().lstrip("\ufeff") != org_id:
                return refuse("org id not confirmed.", show_edition=False)
            environment = "test"
            edition = f"{lic.get('paid_type')} ({lic.get('trial_type')} trial)"
        elif detected != args.env:
            return refuse(
                f"you said --env {args.env} but Zoho issued a token for a {detected} "
                f"environment ({tok.api_domain}). If this is your own unpaid test org, use --env test."
            )
        else:
            org_info = metadata.get_org(reader)
            edition = org_info.get("type")

    secrets.save_refresh_token(slug, tok.refresh_token)
    profile = OrgProfile(
        slug=slug,
        name=args.name,
        environment=environment,
        dc=dc.key,
        accounts_server=callback.accounts_server,
        api_domain=tok.api_domain,
        scopes=(tok.scope.replace(",", " ").split() if tok.scope else list(scopes)),
        zoho_org_id=str(org_info.get("id")) if org_info.get("id") else None,
        edition=edition,
    )
    OrgRegistry().upsert(profile)
    print(f"Connected '{args.name}' as '{slug}' ({environment}, DC {dc.key}, {tok.api_domain}).")
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


def cmd_workspace_set(args: argparse.Namespace) -> int:
    from copilot.projects.workspace import set_workspace_root

    print(f"Projects folder: {set_workspace_root(Path(args.path))}")
    return 0


def cmd_project_new(args: argparse.Namespace) -> int:
    from copilot.projects.workspace import create_project

    if args.org:
        OrgRegistry().get(args.org)  # fail early on a typo
    p = create_project(args.client, args.project, org=args.org)
    print(f"Created {p.slug} at {p.path}")
    print(f"Drop the client's files (.md .txt .eml .docx .pdf) into {p.requirements_dir}")
    return 0


def cmd_project_list(args: argparse.Namespace) -> int:
    from copilot.projects.workspace import list_projects

    projects = list_projects()
    if not projects:
        print("No projects. Run: copilot project new 'Client' 'Project' --org <slug>")
    for p in projects:
        print(f"{p.slug:40} org={p.info.org or '-'}")
    return 0


def cmd_project_ingest(args: argparse.Namespace) -> int:
    from copilot.projects.requirements import ingest_dir
    from copilot.projects.workspace import open_project

    units = ingest_dir(open_project(args.project).requirements_dir)
    for u in units:
        where = f" p{u.page}" if u.page else ""
        print(f"{u.id:28}{where} {u.text[:100]}")
    print(f"\n{len(units)} requirement units")
    return 0


def cmd_plan_show(args: argparse.Namespace) -> int:
    from copilot.planner.store import approved_version, load_plan
    from copilot.projects.workspace import open_project

    p = open_project(args.project)
    plan = load_plan(p, args.version)
    approved = approved_version(p)
    path = p.plans_dir / f"plan.v{plan.version}.md"
    state = "APPROVED" if approved == plan.version else "not approved"
    print(f"{p.slug} plan v{plan.version} ({state}). Read it: {path}")
    print(f"items={len(plan.items)} questions={len(plan.questions)} tickets={len(plan.tickets)}")
    for i in plan.issues:
        print(f"  {i.level}: {i.ref or ''} {i.message}")
    return 0


def cmd_plan_approve(args: argparse.Namespace) -> int:
    from copilot.planner.store import approve, load_plan
    from copilot.projects.workspace import open_project

    p = open_project(args.project)
    plan = load_plan(p, args.version)
    print(f"About to approve {p.slug} plan v{plan.version}: {len(plan.items)} items.")
    print(f"Review first: {p.plans_dir / f'plan.v{plan.version}.md'}")
    # strip a BOM too: PowerShell pipes one in front of redirected input
    typed = input(f"Type the project slug ({p.slug}) to approve: ").strip().lstrip("\ufeff")
    if typed != p.slug:
        print("Not approved.", file=sys.stderr)
        return 2
    a = approve(p, plan.version, note=args.note)
    print(f"Approved v{a.version} by {a.approver} at {a.approved_at:%Y-%m-%d %H:%M} UTC")
    return 0


def cmd_plan_diff(args: argparse.Namespace) -> int:
    from copilot.planner.render import render_change_request
    from copilot.planner.store import approved_version, diff_plans, load_plan
    from copilot.projects.workspace import open_project

    p = open_project(args.project)
    new = load_plan(p, args.new)
    old_v = args.old or approved_version(p) or new.based_on_version
    if not old_v:
        print("Nothing to compare against (no approved or earlier version).", file=sys.stderr)
        return 1
    old = load_plan(p, old_v)
    md = render_change_request(diff_plans(old, new), old, new)
    out = p.exports_dir / f"change-request-v{old.version}-to-v{new.version}.md"
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"Saved: {out}")
    return 0


def cmd_plan_export(args: argparse.Namespace) -> int:
    from copilot.planner.render import render_signoff
    from copilot.planner.store import load_plan
    from copilot.planner.uat import render_uat_script
    from copilot.projects.requirements import load_units
    from copilot.projects.workspace import open_project

    p = open_project(args.project)
    plan = load_plan(p, args.version)
    units = load_units(p.requirements_dir)
    renderers = {"signoff": render_signoff, "uat": render_uat_script}
    for kind in args.what:
        out = p.exports_dir / f"{kind}-v{plan.version}.md"
        out.write_text(renderers[kind](plan, units, p.info.client), encoding="utf-8")
        print(f"Wrote {out}")
    return 0


def cmd_deluge_check(args: argparse.Namespace) -> int:
    from copilot.deluge import handoff
    from copilot.deluge.checks import check_deluge
    from copilot.planner.service import org_metadata
    from copilot.projects.workspace import open_project

    p = open_project(args.project)
    plan = handoff.plan_for_functions(p)
    fn = handoff.find_function(plan, args.name)
    reader = open_read_client(p.info.org)[1] if p.info.org else None
    catalog = handoff.field_catalog(plan, org_metadata(reader))
    issues = check_deluge(handoff.load_function_code(p, args.name), fn, catalog)
    for i in issues:
        print(f"{i.level:8} {i.ref}: {i.message}")
    errors = sum(i.level == "error" for i in issues)
    print(f"{errors} error(s), {len(issues) - errors} warning(s)")
    return 1 if errors else 0


def cmd_deluge_push(args: argparse.Namespace) -> int:
    from copilot.deluge.push import PushRefusedError, push_function
    from copilot.projects.workspace import open_project

    def confirm(prompt: str) -> bool:
        return input(prompt).strip().lstrip("\ufeff") == args.name

    try:
        out = push_function(open_project(args.project), args.name, confirm)
    except PushRefusedError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if not out["pushed"]:
        print(f"Not pushed: {out.get('reason') or out.get('code')}: {out.get('message') or ''}")
        if out.get("details"):
            print(json.dumps(out["details"], indent=2))
        if out.get("fallback"):
            print(out["fallback"])
        return 1
    print(f"{out['action'].title()}d '{args.name}' in {out['org']} (plan v{out['plan_version']}).")
    print("Not attached to any workflow: attach it after approval, using the handoff checklist.")
    return 0


def _typed(expected: str):
    def ask(prompt: str) -> bool:
        return input(prompt).strip().lstrip("﻿") == expected

    return ask


def cmd_changes_preview(args: argparse.Namespace) -> int:
    from copilot.changes.apply import ApplyRefusedError, preview
    from copilot.changes.build import render_preview
    from copilot.projects.workspace import open_project

    try:
        cs = preview(open_project(args.project))
    except ApplyRefusedError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(render_preview(cs))
    return 1 if cs.errors else 0


def cmd_changes_list(args: argparse.Namespace) -> int:
    from copilot.changes.apply import list_changesets
    from copilot.projects.workspace import open_project

    for cs in list_changesets(open_project(args.project)):
        done = sum(o.status == "applied" for o in cs.operations)
        print(
            f"{cs.id}  plan v{cs.plan_version}  {cs.status:20} {done}/{len(cs.operations)} applied  org={cs.org}"
        )
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    from copilot.changes.apply import ApplyRefusedError, apply, changesets_dir
    from copilot.changes.build import render_preview
    from copilot.projects.workspace import open_project

    p = open_project(args.project)

    def offer_rollback(cs) -> bool:
        applied = sum(o.status == "applied" for o in cs.operations)
        print(render_preview(cs))
        return _typed("ROLLBACK")(
            f"Stopped at a failure after {applied} applied operation(s). "
            "Type ROLLBACK to undo them (soft: hide/deactivate), or anything else to keep them: "
        )

    try:
        cs = apply(p, _typed(p.slug), offer_rollback)
    except ApplyRefusedError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if not cs.operations:
        print(
            "Nothing to apply: the org already matches the approved plan (or only handoff items remain)."
        )
        return 0
    if cs.status == "preview":
        print("Not applied.")
        return 1
    print(render_preview(cs))
    print(f"Saved: {changesets_dir(p) / cs.id}")
    return 0 if cs.status == "applied" else 1


def cmd_rollback(args: argparse.Namespace) -> int:
    from copilot.changes.apply import ApplyRefusedError, rollback
    from copilot.changes.build import render_preview
    from copilot.projects.workspace import open_project

    try:
        cs = rollback(
            open_project(args.project),
            args.changeset,
            hard=args.hard,
            confirm_hard=_typed("DELETE"),
        )
    except ApplyRefusedError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(render_preview(cs))
    return 0 if cs.status == "rolled_back" else 1


def _print_run(run) -> None:
    if run.setup_problem:
        print(f"SETUP: {run.setup_problem}")
    for c in run.cases:
        print(f"{'PASS' if c.passed else 'FAIL'}  {c.name}")
        for prob in c.problems:
            print(f"      - {prob}")
    print(
        f"{run.passed} passed, {run.failed} failed ({run.function}, code {run.code_sha}, auth {run.auth})"
    )


def cmd_deluge_test(args: argparse.Namespace) -> int:
    from copilot.deluge.testloop import TestLoopRefusedError, run_tests
    from copilot.projects.workspace import open_project

    try:
        run = run_tests(open_project(args.project), args.name)
    except TestLoopRefusedError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    _print_run(run)
    return 0 if run.all_passed else 1


def cmd_deluge_loop(args: argparse.Namespace) -> int:
    from copilot.deluge.push import push_function
    from copilot.deluge.testloop import TestLoopRefusedError, fix_loop
    from copilot.projects.workspace import open_project

    p = open_project(args.project)

    def confirm_push(diff: str) -> bool:
        print("\nThe agent saved a fix:\n" + diff)
        return _typed(args.name)(f"Type {args.name} to push this update and re-run the tests: ")

    def push() -> dict:
        out = push_function(p, args.name, lambda _prompt: True)  # already confirmed above
        print(
            "pushed"
            if out.get("pushed")
            else f"push failed: {out.get('code')}: {out.get('message')}"
        )
        return out

    print(f"Running tests for {args.name}. On failure, ask the agent to fix it (get_test_results + save_deluge); "
          f"this loop waits up to {args.wait} s for the saved fix. Ctrl+C to stop.")  # fmt: skip
    try:
        run = fix_loop(p, args.name, confirm_push, push, _print_run, args.max, args.wait)
    except TestLoopRefusedError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0 if run.all_passed else 1


def cmd_deluge_set_url(args: argparse.Namespace) -> int:
    from copilot.deluge.testloop import save_api_key_url
    from copilot.projects.workspace import open_project

    p = open_project(args.project)
    org = OrgRegistry().get(p.info.org)
    url = getpass.getpass(f"Paste the API-key URL of {args.name} (input hidden): ")
    save_api_key_url(org, args.name, url)
    print("Stored in the OS keychain.")
    return 0


def cmd_deluge_metrics(args: argparse.Namespace) -> int:
    from copilot.deluge.testloop import function_metrics
    from copilot.projects.workspace import open_project

    print(json.dumps(function_metrics(open_project(args.project)), indent=2))
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    from copilot.audit.log import AuditLog

    for e in AuditLog().entries(org=args.org, limit=args.limit):
        print(
            f"{e['ts'][:19]} {e['actor']:10} {e['org']:16} {e['action']:16} {e['target']:24} {e['result']}"
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
    c.add_argument("--env", required=True, choices=["developer", "sandbox", "test", "production"])
    c.add_argument("--port", type=int, default=oauth.DEFAULT_PORT)
    c.add_argument(
        "--allow-writes",
        action="store_true",
        help="also request scopes to create/update Deluge functions (developer/sandbox only)",
    )
    c.add_argument(
        "--allow-test-records",
        action="store_true",
        help="record access for the test loop (needs --test-modules)",
    )
    c.add_argument(
        "--test-modules", help="comma-separated modules the test loop may create fake records in"
    )
    c.add_argument(
        "--no-execute-scope",
        action="store_true",
        help="don't request the (undocumented) function execute scope",
    )
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

    ws = sub.add_parser("workspace", help="where client projects live")
    ws_sub = ws.add_subparsers(dest="ws_cmd", required=True)
    wss = ws_sub.add_parser("set")
    wss.add_argument("path")
    wss.set_defaults(func=cmd_workspace_set)

    pr = sub.add_parser("project", help="client projects")
    pr_sub = pr.add_subparsers(dest="project_cmd", required=True)
    pn = pr_sub.add_parser("new")
    pn.add_argument("client")
    pn.add_argument("project")
    pn.add_argument("--org", help="connected org slug used for validation")
    pn.set_defaults(func=cmd_project_new)
    pr_sub.add_parser("list").set_defaults(func=cmd_project_list)
    pi = pr_sub.add_parser("ingest", help="extract requirement units from the files")
    pi.add_argument("project")
    pi.set_defaults(func=cmd_project_ingest)

    pl = sub.add_parser("plan", help="review, approve, diff and export plans")
    pl_sub = pl.add_subparsers(dest="plan_cmd", required=True)
    ps = pl_sub.add_parser("show")
    ps.add_argument("project")
    ps.add_argument("--version", type=int)
    ps.set_defaults(func=cmd_plan_show)
    pa = pl_sub.add_parser("approve", help="human approval (typed confirmation)")
    pa.add_argument("project")
    pa.add_argument("--version", type=int)
    pa.add_argument("--note")
    pa.set_defaults(func=cmd_plan_approve)
    pd = pl_sub.add_parser("diff", help="change request between versions")
    pd.add_argument("project")
    pd.add_argument("--old", type=int, help="default: latest approved version")
    pd.add_argument("--new", type=int, help="default: latest version")
    pd.set_defaults(func=cmd_plan_diff)
    pe = pl_sub.add_parser("export", help="client sign-off doc and/or UAT script")
    pe.add_argument("project")
    pe.add_argument("what", nargs="+", choices=["signoff", "uat"])
    pe.add_argument("--version", type=int)
    pe.set_defaults(func=cmd_plan_export)

    ch = sub.add_parser("changes", help="preview or list change sets")
    ch_sub = ch.add_subparsers(dest="changes_cmd", required=True)
    cp = ch_sub.add_parser(
        "preview", help="what applying the approved plan would change (read-only)"
    )
    cp.add_argument("project")
    cp.set_defaults(func=cmd_changes_preview)
    cl = ch_sub.add_parser("list")
    cl.add_argument("project")
    cl.set_defaults(func=cmd_changes_list)

    ap = sub.add_parser(
        "apply", help="apply the approved plan to the dev/sandbox org (typed confirm)"
    )
    ap.add_argument("project")
    ap.set_defaults(func=cmd_apply)

    rb = sub.add_parser("rollback", help="undo a change set (soft by default)")
    rb.add_argument("project")
    rb.add_argument("--changeset", help="default: the latest one")
    rb.add_argument(
        "--hard", action="store_true", help="DELETE created fields/workflows (typed DELETE)"
    )
    rb.set_defaults(func=cmd_rollback)

    dl = sub.add_parser("deluge", help="check and push Deluge functions")
    dl_sub = dl.add_subparsers(dest="deluge_cmd", required=True)
    dc = dl_sub.add_parser("check", help="run static checks on a saved function")
    dc.add_argument("project")
    dc.add_argument("name")
    dc.set_defaults(func=cmd_deluge_check)
    dp = dl_sub.add_parser("push", help="save a function in the dev/sandbox org (typed confirm)")
    dp.add_argument("project")
    dp.add_argument("name")
    dp.set_defaults(func=cmd_deluge_push)
    dt = dl_sub.add_parser("test", help="run a function's test cases in the dev/sandbox/test org")
    dt.add_argument("project")
    dt.add_argument("name")
    dt.set_defaults(func=cmd_deluge_test)
    dlp = dl_sub.add_parser("loop", help="test -> agent fixes -> you confirm push -> re-test")
    dlp.add_argument("project")
    dlp.add_argument("name")
    dlp.add_argument("--max", type=int, default=5, help="max test runs")
    dlp.add_argument("--wait", type=float, default=600, help="seconds to wait for a fix")
    dlp.set_defaults(func=cmd_deluge_loop)
    du = dl_sub.add_parser(
        "set-url", help="store a function's API-key URL (fallback if OAuth execute is refused)"
    )
    du.add_argument("project")
    du.add_argument("name")
    du.set_defaults(func=cmd_deluge_set_url)
    dm = dl_sub.add_parser("metrics", help="test runs per function and first-placement pass rate")
    dm.add_argument("project")
    dm.set_defaults(func=cmd_deluge_metrics)

    au = sub.add_parser("audit", help="show the audit log of changes made in Zoho")
    au.add_argument("--org")
    au.add_argument("--limit", type=int, default=50)
    au.set_defaults(func=cmd_audit)

    sub.add_parser("serve", help="run the MCP server (stdio)").set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (
        secrets.MissingSecretError,
        oauth.OAuthError,
        LookupError,
        ValueError,
        FileExistsError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
