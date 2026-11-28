"""`copilot demo`: the whole workflow, offline, against a built-in fake Zoho.

Uses the REAL code paths (ingestion, planner validation, approvals, exports, Deluge checks,
Builder apply/rollback, change requests). Only Zoho is simulated. Everything runs in a
throwaway folder with an in-memory keychain, so your real setup is never touched.
The plan drafts below stand in for what the AI agent writes in Claude Code.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from email.message import EmailMessage
from pathlib import Path

import httpx
import keyring
from keyring.backend import KeyringBackend

from copilot.auth import secrets
from copilot.changes import apply as ap
from copilot.changes.build import render_preview
from copilot.deluge import handoff
from copilot.deluge.checks import check_deluge
from copilot.demo.fakezoho import ACCOUNTS, API_DOMAIN, FakeOrg
from copilot.orgs.registry import OrgProfile, OrgRegistry
from copilot.orgs.session import open_read_client
from copilot.planner import service, store
from copilot.planner.render import render_change_request, render_signoff
from copilot.planner.uat import render_uat_script
from copilot.projects.requirements import ingest_dir, load_units
from copilot.projects.workspace import create_project
from copilot.zoho.write import BUILDER_WRITE_SCOPES, FUNCTION_WRITE_SCOPES

KICKOFF = """# Leads
- Agents must flag how hot each lead is.
- Trade shows are now a lead source.
- Hot leads need a call task within one day.
- Post hot leads to our Slack channel.

# Deals
- Deals must show the property in the main section.
- Commission can never be negative.
- REQ-9: sync new listings from our website every night.
"""

GOOD_DELUGE = """void automation.post_hot_lead(String lead_id)
{
	result = Map();
	lead = zoho.crm.getRecordById("Leads", lead_id.toLong());
	if(lead == null || lead.get("Lead_Temperature") != "Hot")
	{
		info "not a hot lead: " + lead_id;
		return;
	}
	message = Map();
	message.put("text","Hot lead: " + ifnull(lead.get("Last_Name"),"(no name)"));
	response = invokeurl
	[
		url :"https://slack.com/api/chat.postMessage"
		type :POST
		parameters:message.toString()
		connection:"slack_conn"
	];
	result.put("status","ok");
	info result;
}
"""
BAD_DELUGE = """void automation.post_hot_lead(String lead_id)
{
	lead = zoho.crm.getRecordById("Leads", 4150868000000225013);
	response = invokeurl
	[
		url :"https://slack.com/api/chat.postMessage"
		type :POST
		headers:{"Authorization":"Bearer xoxb-123456789012-abcdefghijkl"}
	];
}
"""


class _MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store: dict = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


def _say(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(3, 70 - len(title)))


def _email(path: Path, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"], msg["From"] = subject, "ops@acme-realty.test"
    msg.set_content(body)
    path.write_bytes(msg.as_bytes())


def _uid(units, start: str) -> str:
    return next(u.id for u in units if u.text.startswith(start))


def _draft(units, mistake: bool) -> dict:
    u = lambda s: _uid(units, s)  # noqa: E731
    return {
        "summary": "Track lead temperature, add Trade Show as a source, create a call task and a "
        "Slack post for hot leads, and show the property on deals.",
        "items": [
            {"kind": "field", "id": "P-001", "title": "Lead temperature", "rationale": "Agents flag hot leads",
             "sources": [u("Agents must flag")], "estimate_minutes": 10, "module": "Leads",
             "field_label": "Lead Temperature", "api_name": "Lead_Temperature", "data_type": "picklist",
             "picklist_values": ["Hot", "Warm", "Cold"],
             "placement": [{"layout": "Standard", "section": "Lead Information", "required": True}],
             "acceptance_test": "Create a lead and pick Hot"},
            {"kind": "picklist_change", "id": "P-002", "title": "Trade Show source", "rationale": "New channel",
             "sources": [u("Trade shows")], "estimate_minutes": 5, "module": "Leads",
             "field_api_name": "Lead_Source", "add_values": ["Trade Show"],
             "acceptance_test": "Lead Source offers Trade Show"},
            {"kind": "workflow", "id": "P-003", "title": "Hot lead call task", "rationale": "React within a day",
             "sources": [u("Hot leads need")], "depends_on": ["P-001"], "estimate_minutes": 20,
             "module": "Leads", "trigger": "create_or_edit", "criteria": "Lead_Temperature is Hot",
             "conditions": [{"field": "Lead_Score" if mistake else "Lead_Temperature", "comparator": "equal", "value": "Hot"}],
             "actions": [{"type": "task", "details": "call the lead", "task": {"subject": "Call hot lead", "due_in_days": 1}}],
             "acceptance_test": "A Hot lead gets a call task due tomorrow"},
            {"kind": "function", "id": "P-004", "title": "Post hot leads to Slack", "rationale": "Team visibility",
             "sources": [u("Post hot leads")], "estimate_minutes": 45, "name": "post_hot_lead",
             "category": "automation", "purpose": "post a Slack message for a hot lead",
             "arguments": {"lead_id": "Leads.id"},
             "uses_fields": ["Leads.Last_Name", "Leads.Lead_Temperature"], "connections": ["slack_conn"]},
            {"kind": "connection", "id": "P-005", "title": "Slack connection", "rationale": "Needed by P-004",
             "sources": [u("Post hot leads")], "estimate_minutes": 10, "service": "Slack",
             "connection_name": "Slack", "link_name": "slack_conn",
             "scopes": [{"scope": "chat:write", "reason": "post the hot-lead message"}]},
            {"kind": "layout", "id": "P-006", "title": "Property in the main section", "rationale": "Visibility",
             "sources": [u("Deals must show")], "estimate_minutes": 5, "module": "Deals", "layout": "Standard",
             "create_new": False, "changes": ["move Property to Deal Information, required"],
             "field_placements": [{"field": "Property", "section": "Deal Information", "required": True}],
             "acceptance_test": "Property shows at the top of a deal"},
            {"kind": "validation_rule", "id": "P-007", "title": "Commission >= 0", "rationale": "Data quality",
             "sources": [u("Commission can")], "estimate_minutes": 5, "module": "Deals",
             "steps_hint": "Deals > Validation Rules > Commission must be >= 0"},
        ],
        "questions": [
            {"id": "Q-001", "question": "Should the Slack message include the lead's phone number?",
             "why_it_matters": "Decides which fields the function reads", "sources": [u("Post hot leads")]},
        ],
        "tickets": [
            {"id": "T-001", "title": "Nightly listing sync", "description": "Import new website listings",
             "acceptance_criteria": ["New listings appear by 7am"], "estimate_hours": 8,
             "sources": [u("REQ-9")]},
        ],
        "out_of_scope": [
            {"unit_id": u("Please keep"), "reason": "no change needed"},
            {"unit_id": u("Call me on"), "reason": "contact details, not a requirement"},
        ],
    }  # fmt: skip


def run_demo(out: Path | None = None) -> int:
    with contextlib.suppress(
        AttributeError, ValueError
    ):  # old Windows consoles can't print every char
        sys.stdout.reconfigure(errors="replace")
    out = (out or Path(tempfile.mkdtemp(prefix="copilot-demo-"))).resolve()
    out.mkdir(parents=True, exist_ok=True)
    saved_env = {k: os.environ.get(k) for k in ("COPILOT_HOME", "COPILOT_PROJECTS")}
    saved_keyring = keyring.get_keyring()
    os.environ["COPILOT_HOME"], os.environ["COPILOT_PROJECTS"] = (
        str(out / "home"),
        str(out / "projects"),
    )
    keyring.set_keyring(_MemoryKeyring())
    try:
        return _run(out)
    finally:
        keyring.set_keyring(saved_keyring)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _run(out: Path) -> int:
    fake = FakeOrg()
    http = httpx.Client(transport=fake.transport())
    print(
        "Zoho Implementer Copilot: offline demo. Zoho is simulated; everything else is the real code."
    )
    print(f"Output folder: {out}")

    _say("1. A (fake) client org is connected")
    secrets.save_client(secrets.ClientCredentials("demo-client", {"us": "demo-secret"}))
    secrets.save_refresh_token("acme-demo", "demo-refresh")
    OrgRegistry().upsert(OrgProfile(
        slug="acme-demo", name="Acme Realty (demo)", environment="test", dc="us", accounts_server=ACCOUNTS,
        api_domain=API_DOMAIN, scopes=["ZohoCRM.settings.ALL", *FUNCTION_WRITE_SCOPES, *BUILDER_WRITE_SCOPES],
    ))  # fmt: skip
    _, reader = open_read_client("acme-demo", http=http)
    print(
        "[ok] org 'acme-demo' (test org). Real use: copilot connect (OAuth, PKCE, tokens in the OS keychain)"
    )

    _say("2. The client sends requirements")
    p = create_project("Acme Realty", "Onboarding", org="acme-demo")
    (p.requirements_dir / "kickoff.md").write_text(KICKOFF, encoding="utf-8")
    _email(p.requirements_dir / "kickoff-email.eml", "Follow-up",
           "Hi team,\nPlease keep the website lead form as it is.\n"
           "Call me on +1 555 010 9999 if anything is unclear.\n--\nSara, Acme Ops\n")  # fmt: skip
    units = ingest_dir(p.requirements_dir)
    for u in units:
        print(f"  {u.id:18} {u.text}")
    print(
        f"[ok] {len(units)} requirement units; phone number masked, greeting and signature dropped"
    )

    _say("3. The agent drafts a plan; validation catches a made-up field")
    first = service.check(p, _draft(units, mistake=True), lambda: reader)
    for e in first["errors"]:
        print(f"  [x] {e['ref']}: {e['message']}")
    saved = service.save(p, _draft(units, mistake=False), lambda: reader)
    print(f"[ok] fixed and saved plan v{saved['version']}: coverage {saved['coverage']}")
    print(f"     every item cites the client's sentence; read it: {saved['plan_md']}")

    _say("4. You approve the plan")
    store.approve(p, 1, note="demo")
    print(
        "[ok] approved v1 (real use: copilot plan approve, you type the project slug; the agent can't approve)"
    )

    _say("5. Deliverables for the client")
    plan = store.load_plan(p)
    units = load_units(p.requirements_dir)
    (p.exports_dir / "signoff-v1.md").write_text(
        render_signoff(plan, units, "Acme Realty"), encoding="utf-8"
    )
    (p.exports_dir / "uat-v1.md").write_text(
        render_uat_script(plan, units, "Acme Realty"), encoding="utf-8"
    )
    print(f"[ok] sign-off doc and UAT script in {p.exports_dir}")

    _say("6. Deluge: static checks before anything reaches Zoho")
    catalog = handoff.field_catalog(plan, service.org_metadata(reader))
    fn = handoff.find_function(plan, "post_hot_lead")
    for i in check_deluge(BAD_DELUGE, fn, catalog):
        print(f"  [{'x' if i.level == 'error' else '!'}] {i.ref}: {i.message}")
    good = check_deluge(GOOD_DELUGE, fn, catalog)
    print(f"[ok] fixed version: {sum(i.level == 'error' for i in good)} errors")
    handoff.save_function(p, "post_hot_lead", GOOD_DELUGE, [])
    print(f"     saved with a placement header + connections.md in {p.path / 'handoff'}")
    print(
        "     (real use: copilot deluge push / deluge loop on a sandbox; never attached to a workflow automatically)"
    )

    _say("7. Builder: preview, apply, roll back")
    cs = ap.preview(p, http=http)
    print(render_preview(cs))
    cs = ap.apply(p, confirm=lambda _prompt: True, offer_rollback=lambda _cs: False, http=http)
    print(
        f"[ok] applied {sum(o.status == 'applied' for o in cs.operations)} operations (real use: you type the slug)"
    )
    print(
        f"     Leads.Lead_Temperature -> {fake.layout_field('Leads', 'Lead_Temperature')} (section, required)"
    )
    print(f"     Deals.Property         -> {fake.layout_field('Deals', 'Property')}")
    print(
        f"     workflow rules active  -> {[w['name'] for w in fake.workflows.values() if w['status']['active']]}"
    )
    ap.rollback(p, http=http)
    print(
        "[ok] soft rollback: field hidden (data kept), layout restored, rule deactivated, nothing deleted"
    )
    print(f"     Deals.Property         -> {fake.layout_field('Deals', 'Property')}")
    print(
        f"     workflow rules active  -> {[w['name'] for w in fake.workflows.values() if w['status']['active']]}"
    )

    _say("8. The client changes their mind: a change request")
    _email(p.requirements_dir / "cr-viewings.eml", "Change request",
           "We also need agents to log feedback after each property viewing.\n"
           "Please drop the Slack idea for now.\n")  # fmt: skip
    units = load_units(p.requirements_dir)
    v2 = _draft(units, mistake=False)
    v2["items"] = [i for i in v2["items"] if i["id"] not in ("P-004", "P-005")]
    v2["items"].append({"kind": "field", "id": "P-008", "title": "Viewing feedback", "rationale": "Learn from viewings",
                        "sources": [_uid(units, "We also need")], "estimate_minutes": 15, "module": "Deals",
                        "field_label": "Viewing Feedback", "api_name": "Viewing_Feedback", "data_type": "textarea"})  # fmt: skip
    v2["questions"] = []
    v2["out_of_scope"] += [{"unit_id": _uid(units, "Post hot leads"), "reason": "withdrawn by the client"},
                           {"unit_id": _uid(units, "Please drop"), "reason": "the withdrawal itself"}]  # fmt: skip
    saved2 = service.save(p, v2, lambda: reader)
    old, new = store.load_plan(p, 1), store.load_plan(p, saved2["version"])
    cr = render_change_request(store.diff_plans(old, new), old, new)
    (p.exports_dir / "change-request-v1-to-v2.md").write_text(cr, encoding="utf-8")
    print(cr)

    _say("Done")
    print(f"Open the files: {p.path}")
    print("  plans/plan.v1.md, plans/plan.v2.md   exports/   handoff/   changesets/")
    print(f"Audit log of every (fake) Zoho write: {out / 'home' / 'audit.sqlite'}")
    return 0
