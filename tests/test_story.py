"""The full story, end to end, against a fake Zoho.

An implementer onboards Acme Realty:
  connect the dev org -> snapshot -> new project -> client sends a .docx and an email
  -> the agent drafts a plan over MCP, makes a mistake, fixes it, saves v1
  -> the human approves at the CLI -> sign-off doc + UAT script for the client
  -> the client sends a change request -> the agent saves v2 -> change-request diff
  -> the project ends and the client's access is revoked.

Only Zoho's servers are faked (respx). Everything else is the real code, using the real
PKCE, the real localhost callback server, the keychain layer (in-memory backend), the
CLI, the MCP server and the planner.
"""

import asyncio
import builtins
import json
from email.message import EmailMessage

import docx
import fake_zoho
import keyring
import respx
from test_e2e import free_port

from copilot import cli
from copilot.auth import secrets
from copilot.mcp_server import server as mcp_mod
from copilot.orgs.registry import OrgRegistry

SLUG = "acme-realty/onboarding"
ORG = "acme-dev"


def call(name, **args):
    r = asyncio.run(mcp_mod.server.call_tool(name, args))
    assert not r.is_error, r
    if r.structured_content is not None:
        sc = r.structured_content
        return sc["result"] if set(sc) == {"result"} else sc
    return json.loads(r.content[0].text)


def unit_id(units, starts_with):
    matches = [u["id"] for u in units if u["text"].startswith(starts_with)]
    assert len(matches) == 1, (starts_with, [u["text"] for u in units])
    return matches[0]


def write_email(path, subject, body):
    msg = EmailMessage()
    msg["Subject"], msg["From"] = subject, "ops@acme-realty.test"
    msg.set_content(body)
    path.write_bytes(msg.as_bytes())


def fake_browser_approves(url):
    """The client's admin clicks Accept: Zoho redirects to our localhost callback."""
    import threading
    import time
    import urllib.request
    from urllib.parse import parse_qs, urlencode, urlsplit

    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert len(q["code_challenge"]) == 43  # real PKCE S256 challenge
    target = (
        q["redirect_uri"]
        + "?"
        + urlencode(
            {
                "code": "c1",
                "state": q["state"],
                "location": "us",
                "accounts-server": fake_zoho.ACCOUNTS,
            }  # fmt: skip
        )
    )

    def hit():
        for _ in range(50):
            try:
                urllib.request.urlopen(target, timeout=2)  # noqa: S310 (localhost)
                return
            except OSError:
                time.sleep(0.1)

    threading.Thread(target=hit, daemon=True).start()
    return True


@respx.mock
def test_acme_realty_full_story(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("COPILOT_PROJECTS", str(tmp_path / "ZohoProjects"))
    token = respx.post(f"{fake_zoho.ACCOUNTS}/oauth/v2/token").respond(
        200, json=fake_zoho.token_body()
    )
    revoke = respx.post(f"{fake_zoho.ACCOUNTS}/oauth/v2/revoke/token").respond(200)
    fake_zoho.mount(respx.mock)
    mcp_mod._clients.clear()

    # --- 1. implementer stores the API client and connects the dev org -----------------
    secrets.save_client(secrets.ClientCredentials("cid", {"us": "client-secret"}))
    monkeypatch.setattr(cli.webbrowser, "open", fake_browser_approves)
    assert cli.main(["connect", "--name", "Acme Dev", "--slug", ORG, "--env", "developer",
                     "--port", str(free_port())]) == 0  # fmt: skip
    sent = token.calls[0].request.content.decode()
    assert "code_verifier=" in sent and "client-secret" in sent

    # --- 2. metadata snapshot, users masked ---------------------------------------------
    assert cli.main(["snapshot", ORG, "--out", str(tmp_path / "snapshots")]) == 0
    users = call("get_users", org=ORG)
    assert [u["alias"] for u in users] == ["user_1", "user_2"]
    assert "Jane" not in json.dumps(users) and "demo-realty" not in json.dumps(users)

    # --- 3. new client project, requirement files arrive --------------------------------
    assert cli.main(["project", "new", "Acme Realty", "Onboarding", "--org", ORG]) == 0
    req_dir = tmp_path / "ZohoProjects" / "acme-realty" / "onboarding" / "requirements"
    d = docx.Document()
    d.add_heading("Leads", level=1)
    d.add_paragraph("Website leads must record the buyer's budget range.")
    d.add_paragraph("Flag hot leads so agents can react within an hour.")
    d.add_heading("Deals", level=1)
    d.add_paragraph("Every deal must show the property and its asking price.")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "REQ-9", "Sync new listings nightly"
    d.save(req_dir / "kickoff.docx")
    write_email(
        req_dir / "kickoff-email.eml",
        "Follow-up from the kickoff",
        "Hi team,\nPlease post hot leads to our Slack channel as well.\n"
        "Call me on +1 555 010 9999 if anything is unclear.\n--\nSara, Acme Ops\n",
    )
    assert cli.main(["project", "ingest", SLUG]) == 0

    # --- 4. the agent reads requirements over MCP ---------------------------------------
    units = call("get_requirements", project=SLUG)
    assert "555" not in json.dumps(units) and "Sara" not in json.dumps(units)
    u_budget = unit_id(units, "Website leads")
    u_hot = unit_id(units, "Flag hot leads")
    u_deal = unit_id(units, "Every deal")
    u_sync = unit_id(units, "REQ-9")
    u_slack = unit_id(units, "Please post hot leads")
    u_call = unit_id(units, "Call me on")
    assert "[phone]" in next(u["text"] for u in units if u["id"] == u_call)

    plan = {
        "summary": "Qualify leads by budget, alert agents on hot leads, show property data "
        "on deals, and post hot leads to Slack.",
        "items": [
            {"kind": "picklist_change", "id": "P-001", "title": "Budget ranges",
             "rationale": "Qualify leads", "sources": [u_budget], "estimate_minutes": 10,
             "module": "Leads", "field_api_name": "Budget_Range",
             "add_values": ["200k-500k", ">500k"],
             "acceptance_test": "Pick 200k-500k on a lead and save"},
            {"kind": "field", "id": "P-002", "title": "Lead temperature",
             "rationale": "Mark hot leads", "sources": [u_hot], "estimate_minutes": 10,
             "module": "Leads", "field_label": "Lead Temperature",
             "api_name": "Lead_Temperature", "data_type": "picklist",
             "picklist_values": ["Hot", "Warm", "Cold"],
             "acceptance_test": "Set a lead to Hot"},
            {"kind": "workflow", "id": "P-003", "title": "Hot lead task",
             "rationale": "React within an hour", "sources": [u_hot], "depends_on": ["P-002"],
             "estimate_minutes": 20, "module": "Leads", "trigger": "create_or_edit",
             "criteria": "Lead_Temperature is Hot",
             "criteria_fields": ["Leads.Lead_Score"],  # <- mistake: field doesn't exist
             "actions": [{"type": "task", "details": "call within 1 hour, owner = lead owner"}],
             "acceptance_test": "A Hot lead gets a call task due in 1 hour"},
            {"kind": "layout", "id": "P-004", "title": "Property on deals",
             "rationale": "Agents see what they sell", "sources": [u_deal],
             "estimate_minutes": 15, "module": "Deals", "layout": "Standard",
             "create_new": False, "changes": ["add Property lookup to Deal Information"],
             "acceptance_test": "Open a deal and see the property"},
            {"kind": "function", "id": "P-005", "title": "Post hot leads to Slack",
             "rationale": "Team visibility", "sources": [u_slack], "estimate_minutes": 45,
             "name": "post_hot_lead", "category": "automation", "purpose": "Slack message",
             "uses_fields": ["Leads.Last_Name", "Leads.Lead_Temperature"],
             "connections": ["slack_conn"]},
            {"kind": "connection", "id": "P-006", "title": "Slack connection",
             "rationale": "Needed by P-005", "sources": [u_slack], "estimate_minutes": 10,
             "service": "Slack", "connection_name": "Slack", "link_name": "slack_conn",
             "scopes": [{"scope": "chat:write", "reason": "post the hot-lead message"}]},
        ],
        "questions": [
            {"id": "Q-001", "question": "Should the deal show the asking price, or the agreed price?",
             "why_it_matters": "Decides whether we add a new currency field",
             "sources": [u_deal]},
        ],
        "tickets": [
            {"id": "T-001", "title": "Nightly listing sync", "description": "Website to Properties",
             "acceptance_criteria": ["New listings appear by 7am"], "estimate_hours": 8,
             "sources": [u_sync]},
        ],
        "out_of_scope": [{"unit_id": u_call, "reason": "contact details, not a requirement"}],
    }  # fmt: skip

    # --- 5. agent checks, sees the mistake, fixes it, saves v1 ---------------------------
    first = call("check_plan", project=SLUG, plan=plan)
    assert first["ok"] is False
    assert any("Leads.Lead_Score" in e["message"] for e in first["errors"])
    plan["items"][2]["criteria_fields"] = ["Leads.Lead_Temperature"]
    saved = call("save_plan", project=SLUG, plan=plan)
    assert saved["saved"] and saved["version"] == 1
    assert saved["coverage"] == {"covered": 5, "out_of_scope": 1}

    # --- 6. human approves at the CLI (the agent has no approve tool) --------------------
    monkeypatch.setattr(builtins, "input", lambda prompt="": "wrong/slug")
    assert cli.main(["plan", "approve", SLUG]) == 2
    monkeypatch.setattr(builtins, "input", lambda prompt="": SLUG)
    assert cli.main(["plan", "approve", SLUG, "--note", "signed on kickoff call"]) == 0

    # --- 7. client deliverables ---------------------------------------------------------
    assert cli.main(["plan", "export", SLUG, "signoff", "uat"]) == 0
    exports = req_dir.parent / "exports"
    signoff = (exports / "signoff-v1.md").read_text(encoding="utf-8")
    uat = (exports / "uat-v1.md").read_text(encoding="utf-8")
    assert "> Flag hot leads so agents can react within an hour." in signoff
    assert "To confirm: Should the deal show the asking price" in signoff
    assert "- [ ] P-003 Hot lead task: A Hot lead gets a call task due in 1 hour" in uat
    assert "P-005 Post hot leads to Slack: (no acceptance test, ask the implementer)" in uat

    # --- 8. change request: viewing feedback, and Slack is dropped -----------------------
    write_email(
        req_dir / "cr-viewings.eml",
        "Change request",
        "We now also need agents to log feedback after each property viewing.\n"
        "Please drop the Slack idea for now.\n",
    )
    units = call("get_requirements", project=SLUG)
    assert unit_id(units, "Flag hot leads") == u_hot  # old ids did not move
    u_feedback = unit_id(units, "We now also need")
    u_drop = unit_id(units, "Please drop the Slack")

    v2 = json.loads(json.dumps(plan))
    v2["items"] = [i for i in v2["items"] if i["id"] not in ("P-005", "P-006")]
    v2["items"].append(
        {"kind": "field", "id": "P-007", "title": "Viewing feedback", "rationale": "Learn from viewings",
         "sources": [u_feedback], "estimate_minutes": 15, "module": "Deals",
         "field_label": "Viewing Feedback", "api_name": "Viewing_Feedback",
         "data_type": "textarea", "acceptance_test": "Write feedback on a deal"}
    )  # fmt: skip
    v2["questions"] = []  # answered: asking price
    v2["items"][3]["changes"].append("add Asking Price (from Property) to Deal Information")
    v2["items"][3]["estimate_minutes"] = 25
    v2["out_of_scope"] += [
        {"unit_id": u_slack, "reason": f"withdrawn by the client ({u_drop})"},
        {"unit_id": u_drop, "reason": "withdrawal of the Slack request"},
    ]
    saved2 = call("save_plan", project=SLUG, plan=v2)
    assert saved2["saved"] and saved2["version"] == 2

    # --- 9. change-request diff vs the APPROVED version ---------------------------------
    capsys.readouterr()
    assert cli.main(["plan", "diff", SLUG]) == 0
    cr = (exports / "change-request-v1-to-v2.md").read_text(encoding="utf-8")
    assert "P-007" in cr and "Viewing feedback" in cr
    assert "P-005" in cr and "P-006" in cr and "Removed" in cr
    assert "P-004" in cr and "+10m" in cr
    assert "Resolved: Q-001" in cr
    assert "-30 min configuration" in cr  # +15 (P-007) +10 (P-004) -45 (P-005) -10 (P-006)

    # --- 10. project ends: revoke the client's grant ------------------------------------
    assert cli.main(["orgs", "remove", ORG, "--revoke"]) == 0
    assert revoke.called
    assert OrgRegistry().list() == []
    assert keyring.get_password(secrets.SERVICE, f"org:{ORG}:refresh") is None
