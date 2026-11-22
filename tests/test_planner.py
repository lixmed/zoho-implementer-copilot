import asyncio
import copy
import json

import fake_zoho
import pytest
import respx
from test_requirements import MD

from copilot.auth import secrets
from copilot.mcp_server import server as mcp_mod
from copilot.orgs.registry import OrgProfile, OrgRegistry
from copilot.planner import service, store
from copilot.planner.render import render_change_request, render_signoff
from copilot.planner.schema import PlanDraft
from copilot.planner.validate import OrgMetadata, validate
from copilot.projects.requirements import load_units
from copilot.projects.workspace import create_project, open_project

ORG = OrgMetadata(
    fields_by_module={
        m: {f["api_name"] for f in body["fields"]} for m, body in fake_zoho.FIELDS.items()
    }
)

GOOD = {
    "summary": "Capture lead temperature, alert agents on hot leads, show properties on deals.",
    "items": [
        {"kind": "picklist_change", "id": "P-001", "title": "Budget ranges",
         "rationale": "Agents qualify leads by budget", "sources": ["kickoff:2"],
         "estimate_minutes": 10, "module": "Leads", "field_api_name": "Budget_Range",
         "add_values": ["200k-500k", ">500k"]},
        {"kind": "layout", "id": "P-002", "title": "Show Property on deals",
         "rationale": "Agents see the property", "sources": ["kickoff:3"],
         "estimate_minutes": 15, "module": "Deals", "layout": "Standard", "create_new": False,
         "changes": ["add Property to Deal Information"],
         "acceptance_test": "Open a deal and see the Property field"},
        {"kind": "workflow", "id": "P-003", "title": "Hot lead email",
         "rationale": "Agents react fast", "sources": ["kickoff:4"], "depends_on": ["P-004"],
         "estimate_minutes": 20, "module": "Leads", "trigger": "create_or_edit",
         "criteria": "Lead_Temperature is Hot", "criteria_fields": ["Leads.Lead_Temperature"],
         "actions": [{"type": "email_notification", "details": "email lead owner"}]},
        {"kind": "field", "id": "P-004", "title": "Lead temperature", "rationale": "Mark hot leads",
         "sources": ["kickoff:4"], "estimate_minutes": 10, "module": "Leads",
         "field_label": "Lead Temperature", "api_name": "Lead_Temperature",
         "data_type": "picklist", "picklist_values": ["Hot", "Warm", "Cold"]},
        {"kind": "function", "id": "P-005", "title": "Post hot leads to Slack",
         "rationale": "Team visibility", "sources": ["kickoff:4"], "estimate_minutes": 45,
         "name": "notify_slack", "category": "automation", "purpose": "post a message",
         "uses_fields": ["Leads.Lead_Temperature", "Leads.Last_Name"],
         "connections": ["slack_conn"]},
        {"kind": "connection", "id": "P-006", "title": "Slack connection",
         "rationale": "needed by P-005", "sources": ["kickoff:4"], "estimate_minutes": 10,
         "service": "Slack", "connection_name": "Slack", "link_name": "slack_conn",
         "scopes": [{"scope": "chat:write", "reason": "post the hot-lead message"}]},
    ],
    "questions": [
        {"id": "Q-001", "question": "Which budget ranges do you use?",
         "why_it_matters": "Defines the picklist", "blocks": ["P-001"], "sources": ["kickoff:2"]}
    ],
    "tickets": [
        {"id": "T-001", "title": "Property sync from listing site", "description": "nightly import",
         "acceptance_criteria": ["new listings appear within 24h"], "estimate_hours": 6,
         "sources": ["kickoff:3"]}
    ],
    "out_of_scope": [
        {"unit_id": "kickoff:1", "reason": "Lead_Source already has Website and Referral"},
        {"unit_id": "kickoff:5", "reason": "contact details, not a requirement"},
    ],
}  # fmt: skip


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_PROJECTS", str(tmp_path / "projects"))
    p = create_project("Acme Realty", "Phase 1", org="demo-dev-org")
    (p.requirements_dir / "kickoff.md").write_text(MD, encoding="utf-8")
    return p


def draft(data=None) -> PlanDraft:
    return PlanDraft.model_validate(data or GOOD)


def errors(issues):
    return [i.message for i in issues if i.level == "error"]


def test_good_plan_validates_with_derived_delivery(project):
    units = load_units(project.requirements_dir)
    issues, delivery, coverage = validate(draft(), units, ORG)
    assert errors(issues) == []
    assert delivery == {"P-001": "decision", "P-002": "api", "P-003": "api",
                        "P-004": "api", "P-005": "api", "P-006": "handoff"}  # fmt: skip
    status = {c.unit_id: c.status for c in coverage}
    assert status == {"kickoff:1": "out_of_scope", "kickoff:2": "covered",
                      "kickoff:3": "covered", "kickoff:4": "covered",
                      "kickoff:5": "out_of_scope"}  # fmt: skip
    assert any("email template" in i.message for i in issues if i.level == "warning")


def mutate(fn):
    data = copy.deepcopy(GOOD)
    fn(data)
    return draft(data)


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda d: d["items"][0].update(sources=["kickoff:99"]), "not a requirement unit"),
        (lambda d: d["out_of_scope"].pop(), "not addressed"),
        (lambda d: d["items"][3].update(api_name="Budget_Range"), "already exists"),
        (lambda d: d["items"][2].update(criteria_fields=["Leads.Nope"]), "does not exist"),
        (lambda d: d["items"][2].update(criteria_fields=["Leads"]), "must look like"),
        (lambda d: d["items"][2].update(depends_on=["P-404"]), "unknown id"),
        (lambda d: d["items"][3].update(data_type="lookup"), "needs lookup_module"),
        (lambda d: d["items"][1].update(module="Ghosts"), "does not exist"),
        (lambda d: d["items"][1].update(id="P-001"), "duplicate id"),
    ],
)
def test_validation_errors(project, change, expected):
    units = load_units(project.requirements_dir)
    issues, _, _ = validate(mutate(change), units, ORG)
    assert any(expected in m for m in errors(issues)), errors(issues)


def test_unavailable_org_skips_name_checks_but_warns(project):
    units = load_units(project.requirements_dir)
    bad = mutate(lambda d: d["items"][2].update(criteria_fields=["Leads.Nope"]))
    issues, _, _ = validate(bad, units, OrgMetadata(available=False))
    assert errors(issues) == []
    assert any("NOT checked" in i.message for i in issues)


def test_schema_errors_are_reported_not_raised():
    data = copy.deepcopy(GOOD)
    data["items"][5]["link_name"] = "Slack Conn!"
    data["items"][0]["sources"] = []
    parsed, issues = service.parse_draft(data)
    assert parsed is None
    assert len(issues) >= 2


def test_save_versions_approve_and_tamper_detection(project):
    units = load_units(project.requirements_dir)
    v1 = store.save_plan(project, draft(), units, ORG)
    assert v1.version == 1 and v1.based_on_version is None
    md = (project.plans_dir / "plan.v1.md").read_text(encoding="utf-8")
    assert "Requirements coverage" in md and "kickoff:4" in md and "chat:write" in md

    with pytest.raises(store.PlanRejectedError):
        store.save_plan(project, mutate(lambda d: d["out_of_scope"].clear()), units, ORG)
    assert store.versions(project) == [1]

    store.approve(project, 1, note="ok from client call")
    assert store.approved_version(project) == 1
    path = project.plans_dir / "plan.v1.json"
    path.write_text(path.read_text(encoding="utf-8").replace("Budget ranges", "Edited"), "utf-8")
    assert store.approved_version(project) is None  # edited after approval -> not approved


def test_change_request_diff(project):
    units = load_units(project.requirements_dir)
    v1 = store.save_plan(project, draft(), units, ORG)

    def revise(d):
        d["items"] = [i for i in d["items"] if i["id"] not in ("P-005", "P-006")]
        d["items"][1]["estimate_minutes"] = 25
        d["items"].append(
            {"kind": "validation_rule", "id": "P-007", "title": "Commission must be positive",
             "rationale": "data quality", "sources": ["kickoff:3"], "estimate_minutes": 5,
             "module": "Deals", "steps_hint": "Deals > Validation Rules > Commission > 0"}
        )  # fmt: skip
        d["questions"] = []
        d["items"][0]["sources"] = ["kickoff:2"]

    v2 = store.save_plan(project, mutate(revise), units, ORG)
    assert v2.based_on_version == 1
    diff = store.diff_plans(v1, v2)
    assert [a["id"] for a in diff.added] == ["P-007"]
    assert [r["id"] for r in diff.removed] == ["P-005", "P-006"]
    assert [c.id for c in diff.changed] == ["P-002"]
    assert diff.changed[0].fields == {"estimate_minutes": (15, 25)}
    assert diff.estimate_delta_minutes == 5 - 45 - 10 + 10
    assert diff.questions_resolved == ["Q-001"]

    cr = render_change_request(diff, v1, v2)
    assert "P-007" in cr and "Removed" in cr and "-45m" in cr


def test_signoff_quotes_client_words(project):
    units = load_units(project.requirements_dir)
    plan = store.save_plan(project, draft(), units, ORG)
    doc = render_signoff(plan, units, "Acme Realty")
    assert "> Send the agent an email when a hot lead arrives" in doc
    assert "To confirm: Which budget ranges" in doc
    assert "Not included" in doc and "Sign-off" in doc


@respx.mock
def test_mcp_planner_flow_against_fake_org(project):
    secrets.save_client(secrets.ClientCredentials("cid", {"us": "sec"}))
    secrets.save_refresh_token("demo-dev-org", "rt")
    OrgRegistry().upsert(
        OrgProfile(
            slug="demo-dev-org",
            name="Demo",
            environment="developer",
            dc="us",
            accounts_server=fake_zoho.ACCOUNTS,
            api_domain=fake_zoho.DEV_API,
            scopes=[],
        )  # fmt: skip
    )
    respx.post(f"{fake_zoho.ACCOUNTS}/oauth/v2/token").respond(200, json=fake_zoho.token_body())
    fake_zoho.mount(respx.mock)
    mcp_mod._clients.clear()

    def call(name, **args):
        r = asyncio.run(mcp_mod.server.call_tool(name, args))
        assert not r.is_error, r
        if r.structured_content is not None:
            return r.structured_content
        return json.loads(r.content[0].text)

    reqs = call("get_requirements", project=project.slug)
    assert "kickoff:4" in json.dumps(reqs)

    bad = copy.deepcopy(GOOD)
    bad["items"][2]["criteria_fields"] = ["Leads.Lead_Score"]
    checked = call("check_plan", project=project.slug, plan=bad)
    assert checked["ok"] is False
    assert any("Lead_Score" in e["message"] for e in checked["errors"])

    saved = call("save_plan", project=project.slug, plan=GOOD)
    assert saved["saved"] is True and saved["version"] == 1
    assert "copilot plan approve" in saved["next_step"]
    assert call("get_plan", project=project.slug)["version"] == 1
    assert open_project(project.slug).plans_dir.joinpath("plan.v1.md").exists()
