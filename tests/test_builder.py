import json

import fake_zoho
import pytest
import respx
from fake_builder import FakeOrg

from copilot.audit.log import AuditLog
from copilot.auth import secrets
from copilot.changes import apply as ap
from copilot.orgs.registry import OrgProfile, OrgRegistry
from copilot.planner import store
from copilot.planner.service import org_metadata
from copilot.projects.requirements import load_units
from copilot.projects.workspace import create_project
from copilot.zoho.write import BUILDER_WRITE_SCOPES

REQS = """- Agents must flag how hot a lead is.
- Trade shows are a new lead source.
- Hot leads need a call task within a day.
- Deals must show the property in the main section.
- Commission must never be negative.
"""

PLAN = {
    "summary": "Lead temperature, trade show source, hot-lead task, property on deals.",
    "items": [
        {"kind": "field", "id": "P-001", "title": "Lead temperature", "rationale": "flag hot leads",
         "sources": ["reqs:1"], "estimate_minutes": 10, "module": "Leads", "field_label": "Lead Temperature",
         "api_name": "Lead_Temperature", "data_type": "picklist", "picklist_values": ["Hot", "Warm", "Cold"],
         "placement": [{"layout": "Standard", "section": "Lead Information", "required": True}]},
        {"kind": "picklist_change", "id": "P-002", "title": "Trade Show source", "rationale": "new channel",
         "sources": ["reqs:2"], "estimate_minutes": 5, "module": "Leads", "field_api_name": "Lead_Source",
         "add_values": ["Trade Show"]},
        {"kind": "workflow", "id": "P-003", "title": "Hot lead call task", "rationale": "react within a day",
         "sources": ["reqs:3"], "depends_on": ["P-001"], "estimate_minutes": 20, "module": "Leads",
         "trigger": "create_or_edit", "criteria": "Lead_Temperature is Hot",
         "conditions": [{"field": "Lead_Temperature", "comparator": "equal", "value": "Hot"}],
         "actions": [
             {"type": "task", "details": "call the lead", "task": {"subject": "Call hot lead", "due_in_days": 1}},
             {"type": "field_update", "details": "mark source", "field_update": {"field": "Lead_Source", "value": "Referral"}},
         ]},
        {"kind": "layout", "id": "P-004", "title": "Property in main section", "rationale": "visibility",
         "sources": ["reqs:4"], "estimate_minutes": 5, "module": "Deals", "layout": "Standard",
         "create_new": False, "changes": ["move Property to Deal Information, required"],
         "field_placements": [{"field": "Property", "section": "Deal Information", "required": True}]},
        {"kind": "validation_rule", "id": "P-005", "title": "Commission >= 0", "rationale": "data quality",
         "sources": ["reqs:5"], "estimate_minutes": 5, "module": "Deals", "steps_hint": "Deals > Validation Rules"},
    ],
}  # fmt: skip


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_PROJECTS", str(tmp_path / "projects"))
    secrets.save_client(secrets.ClientCredentials("cid", {"us": "sec"}))
    secrets.save_refresh_token("dev", "rt")
    OrgRegistry().upsert(OrgProfile(
        slug="dev", name="Dev", environment="test", dc="us", accounts_server=fake_zoho.ACCOUNTS,
        api_domain=fake_zoho.DEV_API, scopes=list(BUILDER_WRITE_SCOPES)))  # fmt: skip
    fake = FakeOrg()
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{fake_zoho.ACCOUNTS}/oauth/v2/token").respond(
            200, json=fake_zoho.token_body()
        )
        fake.mount(router)
        p = create_project("Acme", "Builder", org="dev")
        (p.requirements_dir / "reqs.md").write_text(REQS, encoding="utf-8")
        units = load_units(p.requirements_dir)
        from copilot.orgs.session import open_read_client

        meta = org_metadata(open_read_client("dev")[1])
        store.save_plan(p, store.PlanDraft.model_validate(PLAN), units, meta)
        store.approve(p, 1)
        yield p, fake


def yes(_prompt):
    return True


def no(_prompt):
    return False


def test_preview_orders_operations_and_lists_handoff(env):
    p, fake = env
    cs = ap.preview(p)
    assert cs.errors == []
    assert [(o.item_id, o.kind) for o in cs.operations] == [
        ("P-001", "create_field"), ("P-001", "place_field"),
        ("P-002", "picklist_add"),
        ("P-004", "place_field"),
        ("P-003", "create_task"), ("P-003", "create_field_update"), ("P-003", "create_workflow"),
    ]  # fmt: skip
    assert cs.credits == 10 + 1 + 10 + 1 + 1 + 1 + 1
    assert [h.item_id for h in cs.handoff] == ["P-005"]
    rule = cs.operations[-1].request["rule"]
    assert rule["conditions"][0]["instant_actions"]["actions"] == [
        {"id": "$op:op-05", "type": "tasks"}, {"id": "$op:op-06", "type": "field_updates"}]  # fmt: skip
    assert fake.writes == []  # preview never writes


def test_apply_then_soft_rollback(env):
    p, fake = env
    cs = ap.apply(p, yes, no)
    assert cs.status == "applied", [o.error for o in cs.operations]
    assert fake.layout_field("Leads", "Lead_Temperature") == ("Lead Information", True)
    assert fake.layout_field("Deals", "Property") == ("Deal Information", True)
    assert "Trade Show" in [v["display_value"] for v in fake.picklists["f2"]]
    (rule,) = fake.workflows.values()
    assert rule["status"]["active"] and rule["name"] == "Hot lead call task"
    assert {a["kind"] for a in fake.actions.values()} == {"tasks", "field_updates"}
    diff = json.loads((ap.changesets_dir(p) / cs.id / "diff.json").read_text())
    assert diff["fields_added"] == {"Leads": ["Lead_Temperature"]} and diff["workflows_added"] == [
        "Hot lead call task"
    ]
    assert len(AuditLog().entries(org="dev")) == 7

    rb = ap.rollback(p)
    assert rb.status == "rolled_back", [(o.op_id, o.status, o.error) for o in rb.operations]
    assert "Lead_Temperature" in fake.fields["Leads"]  # soft: field kept...
    assert fake.layout_field("Leads", "Lead_Temperature") is None  # ...but hidden
    assert fake.layout_field("Deals", "Property") == ("Other", False)  # restored
    trade = next(v["id"] for v in fake.picklists["f2"] if v["display_value"] == "Trade Show")
    assert fake.unused["f2"] == {trade}  # new value marked unused
    assert rule["status"]["active"] is False and len(fake.actions) == 2  # deactivated, kept
    assert not any(w.startswith("DELETE") for w in fake.writes)


def test_hard_rollback_deletes_after_typed_confirmation(env):
    p, fake = env
    ap.apply(p, yes, no)
    rb = ap.rollback(p, hard=True, confirm_hard=lambda prompt: "ALL DATA" in prompt)
    assert rb.status == "rolled_back"
    assert "Lead_Temperature" not in fake.fields["Leads"]
    assert fake.workflows == {} and fake.actions == {}


def test_hard_without_confirmation_falls_back_to_soft(env):
    p, fake = env
    ap.apply(p, yes, no)
    ap.rollback(p, hard=True, confirm_hard=no)
    assert "Lead_Temperature" in fake.fields["Leads"]
    assert not any(w.startswith("DELETE") for w in fake.writes)


def test_failure_stops_and_offers_rollback(env):
    p, fake = env
    fake.fail_on = "POST /settings/automation/workflow_rules"
    offered = []
    cs = ap.apply(p, yes, lambda c: offered.append(c) or True)
    assert offered and offered[0].operations[-1].status == "failed"
    statuses = [o.status for o in cs.operations]
    assert statuses[:6] == ["rolled_back"] * 6 and statuses[6] == "failed"
    assert fake.layout_field("Leads", "Lead_Temperature") is None


def test_failure_mid_way_marks_rest_not_run(env):
    p, fake = env
    fake.fail_on = "PATCH /settings/fields/f2"  # picklist add fails (op-03)
    cs = ap.apply(p, yes, no)
    assert cs.status == "partial"
    assert [o.status for o in cs.operations] == ["applied", "applied", "failed"] + ["not_run"] * 4
    assert "injected failure" in cs.operations[2].error


def test_second_apply_is_a_no_op_for_done_items(env):
    p, fake = env
    ap.apply(p, yes, no)
    cs = ap.preview(p)
    kinds = [o.kind for o in cs.operations]
    assert (
        "create_field" not in kinds
        and "create_workflow" not in kinds
        and "picklist_add" not in kinds
    )
    assert {s.item_id for s in cs.skipped} >= {"P-001", "P-002", "P-003", "P-004"}


def test_typed_confirmation_required(env):
    p, fake = env
    cs = ap.apply(p, no, no)
    assert cs.status == "preview" and fake.writes == []


def test_gates(env, monkeypatch):
    p, fake = env
    reg = OrgRegistry()
    org = reg.get("dev")
    reg.upsert(org.model_copy(update={"scopes": ["ZohoCRM.settings.fields.CREATE"]}))
    with pytest.raises(ap.ApplyRefusedError, match="lacks write scopes"):
        ap.apply(p, yes, no)
    reg.upsert(org.model_copy(update={"environment": "production"}))
    with pytest.raises(ap.ApplyRefusedError, match="production"):
        ap.apply(p, yes, no)
    reg.upsert(org)
    path = p.plans_dir / "plan.v1.json"
    path.write_text(path.read_text("utf-8").replace("Lead temperature", "edited"), "utf-8")
    with pytest.raises(ap.ApplyRefusedError, match="no approved plan"):
        ap.apply(p, yes, no)
    assert fake.writes == []


FN_WORKFLOW = {
    "kind": "workflow", "id": "P-009", "title": "Post hot lead", "rationale": "slack", "sources": ["reqs:3"],
    "estimate_minutes": 5, "module": "Leads", "trigger": "create_or_edit", "criteria": "Lead_Source is Website",
    "conditions": [{"field": "Lead_Source", "comparator": "equal", "value": "Website"}],
    "actions": [{"type": "function", "details": "post to slack", "function": "post_hot_lead"}],
}  # fmt: skip


def _approve_with(p, extra_items):
    data = {**PLAN, "items": PLAN["items"] + extra_items}
    units = load_units(p.requirements_dir)
    store.save_plan(p, store.PlanDraft.model_validate(data), units, store_org_meta())
    store.approve(p, store.versions(p)[-1])


def store_org_meta():
    from copilot.planner.validate import OrgMetadata

    return OrgMetadata(available=False)


def test_function_attached_workflow_is_created_inactive(env):
    p, fake = env
    fake.org_functions["post_hot_lead"] = "fn-777"
    _approve_with(p, [FN_WORKFLOW])
    cs = ap.preview(p)
    op = next(o for o in cs.operations if o.item_id == "P-009")
    rule = op.request["rule"]
    assert rule["status"] == {"active": False}
    assert rule["conditions"][0]["instant_actions"]["actions"] == [
        {"id": "fn-777", "type": "functions"}
    ]
    assert "INACTIVE" in op.summary and "experimental" in op.summary
    ap.apply(p, yes, no)
    stored = next(w for w in fake.workflows.values() if w["name"] == "Post hot lead")
    assert stored["status"] == {"active": False}
    other = next(w for w in fake.workflows.values() if w["name"] == "Hot lead call task")
    assert other["status"] == {"active": True}  # rules without functions keep their setting


def test_function_must_exist_in_org_before_attaching(env):
    p, fake = env
    _approve_with(p, [FN_WORKFLOW])
    with pytest.raises(ap.ApplyRefusedError, match="push it first"):
        ap.apply(p, yes, no)
    assert fake.writes == []


def test_inactive_flag_without_function(env):
    p, fake = env
    item = {**FN_WORKFLOW, "id": "P-010", "title": "Quiet rule", "active": False,
            "actions": [{"type": "task", "details": "t", "task": {"subject": "Check", "due_in_days": 0}}]}  # fmt: skip
    _approve_with(p, [item])
    ap.apply(p, yes, no)
    assert next(w for w in fake.workflows.values() if w["name"] == "Quiet rule")["status"] == {
        "active": False
    }


def test_unknown_section_is_a_build_error(env):
    p, fake = env
    fake.layouts["Deals"][0]["sections"][0]["display_label"] = "Renamed In UI"
    with pytest.raises(ap.ApplyRefusedError, match="section 'Deal Information' not found"):
        ap.apply(p, yes, no)
    assert fake.writes == []
