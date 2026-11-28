import threading
import time

import fake_zoho
import pytest
import respx
from fake_runtime import FakeRuntime

from copilot.audit.log import AuditLog
from copilot.auth import secrets
from copilot.deluge import handoff
from copilot.deluge import testloop as tl
from copilot.orgs.registry import OrgProfile, OrgRegistry
from copilot.planner import store
from copilot.planner.validate import OrgMetadata
from copilot.projects.requirements import load_units
from copilot.projects.workspace import create_project

PLAN = {
    "summary": "two functions",
    "items": [
        {"kind": "function", "id": "P-001", "title": "Count hot leads", "rationale": "r", "sources": ["r:1"],
         "estimate_minutes": 10, "name": "count_hot", "category": "standalone", "purpose": "count"},
        {"kind": "function", "id": "P-002", "title": "Mark referral", "rationale": "r", "sources": ["r:2"],
         "estimate_minutes": 10, "name": "mark_referral", "category": "automation", "purpose": "set source",
         "arguments": {"lead_id": "Leads.id"}, "uses_fields": ["Leads.Lead_Source"]},
    ],
}  # fmt: skip
CORE = 'string standalone.mark_referral_core(String record_id)\n{\n\tresult = Map();\n\tinfo "x";\n\tresult.put("status","ok");\n\treturn result.toString();\n}\n'
COUNT = 'string standalone.count_hot()\n{\n\tresult = Map();\n\tinfo "x";\n\tresult.put("status","ok");\n\treturn result.toString();\n}\n'
RECORD_TEST = {"name": "sets referral", "module": "Leads", "record": {"Last_Name": "COPILOT TEST"},
               "input": {"record_id": "$record_id"}, "expected": {"status": "ok"},
               "expect_record": {"Lead_Source": "Referral"}}  # fmt: skip


def scopes(*extra):
    return ["ZohoCRM.settings.functions.READ", *extra]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_PROJECTS", str(tmp_path / "projects"))
    secrets.save_client(secrets.ClientCredentials("cid", {"us": "sec"}))
    secrets.save_refresh_token("dev", "rt")
    OrgRegistry().upsert(OrgProfile(
        slug="dev", name="Dev", environment="test", dc="us", accounts_server=fake_zoho.ACCOUNTS,
        api_domain=fake_zoho.DEV_API, scopes=scopes("ZohoCRM.modules.leads.ALL")))  # fmt: skip
    rt = FakeRuntime()
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{fake_zoho.ACCOUNTS}/oauth/v2/token").respond(
            200, json=fake_zoho.token_body()
        )
        rt.mount(router)
        p = create_project("Acme", "Tests", org="dev")
        (p.requirements_dir / "r.md").write_text(
            "- Count the hot leads now.\n- Mark referrals on leads.\n", "utf-8"
        )
        units = load_units(p.requirements_dir)
        store.save_plan(
            p, store.PlanDraft.model_validate(PLAN), units, OrgMetadata(available=False)
        )
        store.approve(p, 1)
        yield p, rt


def save(p, name, code, tests):
    handoff.save_function(p, name, code, [handoff.TestCase(**t) for t in tests])


def set_referral(args, rt):
    rt.records["Leads"][args["record_id"]]["Lead_Source"] = "Referral"
    return {"status": "ok"}


def test_standalone_pass(env):
    p, rt = env
    rt.define("count_hot", lambda a, r: {"status": "ok", "count": 2})
    save(
        p,
        "count_hot",
        COUNT,
        [{"name": "counts", "input": {}, "expected": {"status": "ok", "count": 2}}],
    )
    run = tl.run_tests(p, "count_hot")
    assert run.all_passed and run.passed == 1
    assert rt.calls[0]["auth"] == "oauth" and rt.calls[0]["has_header"]


def test_record_core_pass_and_cleanup(env):
    p, rt = env
    rt.define("mark_referral_core", set_referral)
    save(p, "mark_referral_core", CORE, [RECORD_TEST])
    run = tl.run_tests(p, "mark_referral_core")
    assert run.all_passed, run.cases
    assert rt.calls[0]["args"]["record_id"].isdigit()  # "$record_id" was substituted
    assert run.cases[0].record_after == {"Lead_Source": "Referral"}  # only the compared field
    assert rt.records["Leads"] == {}  # test record deleted
    actions = [e["action"] for e in AuditLog().entries(org="dev")]
    assert actions[:3] == ["delete_test_record", "execute_function", "create_test_record"]


def test_record_core_fail_still_cleans_up(env):
    p, rt = env
    rt.define("mark_referral_core", lambda a, r: {"status": "ok"})  # forgot to update the record
    save(p, "mark_referral_core", CORE, [RECORD_TEST])
    run = tl.run_tests(p, "mark_referral_core")
    assert not run.all_passed
    assert run.cases[0].problems == ["record.Lead_Source = None, expected 'Referral'"]
    assert rt.records["Leads"] == {}


def test_wrong_result_value_reported(env):
    p, rt = env
    rt.define("count_hot", lambda a, r: {"status": "error", "error": "no leads"})
    save(p, "count_hot", COUNT, [{"name": "counts", "input": {}, "expected": {"status": "ok"}}])
    run = tl.run_tests(p, "count_hot")
    assert run.cases[0].problems == ["result['status'] = 'error', expected 'ok'"]


def test_not_rest_enabled_gives_setup_hint(env):
    p, rt = env
    rt.define("count_hot", lambda a, r: {"status": "ok"}, rest=False)
    save(p, "count_hot", COUNT, [{"name": "counts", "input": {}, "expected": {"status": "ok"}}])
    run = tl.run_tests(p, "count_hot")
    assert "turn on REST API" in run.setup_problem and "set-url" in run.setup_problem


def test_api_key_url_fallback_and_validation(env):
    p, rt = env
    org = OrgRegistry().get("dev")
    good = f"{fake_zoho.DEV_API}/crm/v8/functions/count_hot/actions/execute?auth_type=apikey&zapikey=abc123"
    for bad in [
        good.replace("developer.zohoapis.com", "evil.example"),
        good.replace("count_hot", "other_fn"),
        good.replace("&zapikey=abc123", ""),
        good.replace("https://", "http://"),
    ]:
        with pytest.raises(ValueError):
            tl.save_api_key_url(org, "count_hot", bad)
    tl.save_api_key_url(org, "count_hot", good)
    rt.define("count_hot", lambda a, r: {"status": "ok"})
    save(p, "count_hot", COUNT, [{"name": "counts", "input": {}, "expected": {"status": "ok"}}])
    run = tl.run_tests(p, "count_hot")
    assert run.all_passed and run.auth == "api_key_url"
    assert rt.calls[0] == {"name": "count_hot", "args": {}, "auth": "apikey", "has_header": False}


def test_setup_problems(env):
    p, rt = env
    save(p, "count_hot", COUNT, [{"name": "counts", "input": {}, "expected": {"status": "ok"}}])
    assert "not in the org yet" in tl.run_tests(p, "count_hot").setup_problem
    reg = OrgRegistry()
    org = reg.get("dev")
    reg.upsert(org.model_copy(update={"scopes": scopes()}))
    rt.define("mark_referral_core", set_referral)
    save(p, "mark_referral_core", CORE, [RECORD_TEST])
    assert "--allow-test-records" in tl.run_tests(p, "mark_referral_core").setup_problem
    reg.upsert(org.model_copy(update={"environment": "production"}))
    with pytest.raises(tl.TestLoopRefusedError):
        tl.run_tests(p, "count_hot")
    assert rt.records.get("Leads", {}) == {}


def test_core_is_accepted_as_companion_of_automation_function(env):
    p, _ = env
    fn = handoff.find_function(handoff.plan_for_functions(p), "mark_referral_core")
    assert fn.category == "standalone" and "record_id" in fn.arguments
    with pytest.raises(handoff.FunctionNotInPlanError):
        handoff.find_function(
            handoff.plan_for_functions(p), "count_hot_core"
        )  # not an automation fn


def test_history_and_first_placement_metric(env):
    p, rt = env
    rt.define("mark_referral_core", lambda a, r: {"status": "ok"})
    save(p, "mark_referral_core", CORE, [RECORD_TEST])
    tl.run_tests(p, "mark_referral_core")  # fails
    rt.define("mark_referral_core", set_referral)
    save(p, "mark_referral_core", CORE.replace('info "x"', 'info "fixed"'), [RECORD_TEST])
    tl.run_tests(p, "mark_referral_core")  # passes
    rt.define("count_hot", lambda a, r: {"status": "ok"})
    save(p, "count_hot", COUNT, [{"name": "c", "input": {}, "expected": {"status": "ok"}}])
    tl.run_tests(p, "count_hot")
    m = tl.function_metrics(p)
    assert m["functions"]["mark_referral_core"] == {
        "runs": 2,
        "code_versions": 2,
        "passed_first_placement": False,
        "passing_now": True,
    }
    assert m["functions"]["count_hot"]["passed_first_placement"] is True
    assert m["first_placement_pass_rate"] == 0.5


def test_fix_loop_waits_for_fix_confirms_push_and_reruns(env):
    p, rt = env
    rt.define("mark_referral_core", lambda a, r: {"status": "ok"})
    save(p, "mark_referral_core", CORE, [RECORD_TEST])
    reports, diffs = [], []

    def agent_fixes_later():
        time.sleep(0.3)
        save(p, "mark_referral_core", CORE.replace('info "x"', 'info "fixed"'), [RECORD_TEST])

    def push():
        rt.define("mark_referral_core", set_referral)  # the pushed code now behaves correctly
        return {"pushed": True}

    threading.Thread(target=agent_fixes_later, daemon=True).start()
    final = tl.fix_loop(p, "mark_referral_core", confirm_push=lambda d: diffs.append(d) or True,
                        push=push, report=reports.append, wait_seconds=10, poll=0.05)  # fmt: skip
    assert final.all_passed
    assert [r.all_passed for r in reports] == [False, True]
    assert '+\tinfo "fixed";' in diffs[0]


def test_fix_loop_declined_push_stops(env):
    p, rt = env
    rt.define("mark_referral_core", lambda a, r: {"status": "ok"})
    save(p, "mark_referral_core", CORE, [RECORD_TEST])
    threading.Timer(0.2, lambda: save(p, "mark_referral_core", CORE + "\n", [RECORD_TEST])).start()
    pushed = []
    final = tl.fix_loop(p, "mark_referral_core", confirm_push=lambda d: False, push=lambda: pushed.append(1) or {"pushed": True},
                        report=lambda r: None, wait_seconds=10, poll=0.05)  # fmt: skip
    assert not final.all_passed and pushed == []
