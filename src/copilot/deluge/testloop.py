"""Deluge test loop: run a function's test cases in a developer/sandbox/test org.

Per test case:
  1. (record tests) create a fake record with automation suppressed, tracked for cleanup
  2. execute the function (OAuth, or its API-key URL if OAuth is refused)
  3. compare the returned result map (subset match) and, if asked, the record's fields
  4. delete the test record, always (finally)
Results go to handoff/functions/<name>.testrun.json for the agent to read; every attempt is
appended to <name>.history.json, which gives "passed on first placement" for the evals.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import keyring
from pydantic import BaseModel, Field

from copilot.audit.log import AuditLog
from copilot.auth.secrets import SERVICE
from copilot.deluge import handoff
from copilot.orgs.registry import OrgProfile, OrgRegistry
from copilot.orgs.session import open_read_client
from copilot.planner.store import approved_version
from copilot.projects.requirements import mask_contacts
from copilot.projects.workspace import Project
from copilot.zoho.client import ZohoAPIError, ZohoReadClient
from copilot.zoho.write import ZohoWriteClient

TEST_ENVS = ("developer", "sandbox", "test")
MAX_OUTPUT = 2000


class TestLoopRefusedError(PermissionError):
    pass


class CaseResult(BaseModel):
    name: str
    passed: bool
    problems: list[str] = Field(default_factory=list)
    output: str | None = None
    record_after: dict | None = None


class TestRun(BaseModel):
    function: str
    org: str
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    code_sha: str
    auth: str
    passed: int = 0
    failed: int = 0
    cases: list[CaseResult] = Field(default_factory=list)
    setup_problem: str | None = None

    @property
    def all_passed(self) -> bool:
        return self.setup_problem is None and self.failed == 0 and self.passed > 0


# ---------------------------------------------------------------- API-key URL fallback


def _url_key(org: str, name: str) -> str:
    return f"fnurl:{org}:{name}"


def validate_api_key_url(url: str, org: OrgProfile, name: str) -> str:
    """Accept only this function's execute URL on this org's own Zoho API host."""
    parts = urlsplit(url.strip())
    expected_host = urlsplit(org.api_domain).hostname
    if parts.scheme != "https" or parts.hostname != expected_host:
        raise ValueError(f"URL must be https://{expected_host}/...")
    if not re.fullmatch(rf"/crm/v\d+/functions/{re.escape(name)}/actions/execute", parts.path):
        raise ValueError(f"URL must be the execute URL of function {name!r}")
    q = parse_qs(parts.query)
    if q.get("auth_type") != ["apikey"] or not q.get("zapikey"):
        raise ValueError(
            "URL must contain auth_type=apikey and zapikey=... (copy it from Setup > Functions > REST API)"
        )
    return url.strip()


def save_api_key_url(org: OrgProfile, name: str, url: str) -> None:
    keyring.set_password(SERVICE, _url_key(org.slug, name), validate_api_key_url(url, org, name))


def load_api_key_url(org: OrgProfile, name: str) -> str | None:
    url = keyring.get_password(SERVICE, _url_key(org.slug, name))
    return validate_api_key_url(url, org, name) if url else None


# ---------------------------------------------------------------- comparison helpers


def _parse_output(payload: dict) -> tuple[str | None, dict | None]:
    """Execute returns {"code":"success","details":{"output": "<string>"}...}; our functions
    return result.toString(), so the output is usually a JSON map."""
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    out = details.get("output", details.get("userMessage"))
    if out is None:
        return None, None
    text = out if isinstance(out, str) else json.dumps(out)
    try:
        parsed = json.loads(text)
        return text, parsed if isinstance(parsed, dict) else None
    except ValueError:
        return text, None


def _same(a, b) -> bool:
    if isinstance(b, dict) and isinstance(a, dict):
        return all(k in a and _same(a[k], v) for k, v in b.items())
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    if isinstance(a, dict) and "name" in a and isinstance(b, str):  # lookup/owner fields
        return a["name"] == b
    return a == b or str(a) == str(b)


def _subst(value, record_id: str | None):
    if value == "$record_id":
        return record_id
    if isinstance(value, dict):
        return {k: _subst(v, record_id) for k, v in value.items()}
    if isinstance(value, list):
        return [_subst(v, record_id) for v in value]
    return value


def code_sha(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()[:12]


# ---------------------------------------------------------------- run


def _context(project: Project, http):
    if not project.info.org:
        raise TestLoopRefusedError("project has no linked org")
    org = OrgRegistry().get(project.info.org)
    if org.environment not in TEST_ENVS:
        raise TestLoopRefusedError(
            f"org {org.slug} is {org.environment}; tests run only on {', '.join(TEST_ENVS)} orgs"
        )
    _, reader = open_read_client(org.slug, http=http)
    return org, reader


def run_tests(
    project: Project, name: str, http: httpx.Client | None = None, audit: AuditLog | None = None
) -> TestRun:
    org, reader = _context(project, http)
    code = handoff.load_function_code(project, name)
    tests_path = handoff.handoff_dir(project) / f"{name}.tests.json"
    cases = (
        [handoff.TestCase(**t) for t in json.loads(tests_path.read_text("utf-8"))]
        if tests_path.exists()
        else []
    )
    url = load_api_key_url(org, name)
    run = TestRun(
        function=name, org=org.slug, code_sha=code_sha(code), auth="api_key_url" if url else "oauth"
    )

    if not cases:
        run.setup_problem = "no test cases saved (save_deluge with tests)"
        return _finish(project, run)
    record_modules = sorted({c.module for c in cases if c.record})
    missing = [m for m in record_modules if not _has_record_scope(org, m)]
    if missing:
        run.setup_problem = (f"org connected without test-record access for {missing}. Reconnect with "
                             f"--allow-test-records --test-modules {','.join(record_modules)}")  # fmt: skip
        return _finish(project, run)
    fn = next(
        (
            f
            for f in reader.get("/settings/functions", use_cache=False).get("functions", [])
            if f.get("api_name") == name
        ),
        None,
    )
    if not fn:
        run.setup_problem = f"function {name!r} is not in the org yet: push it (copilot deluge push) or paste it from the .dg file"
        return _finish(project, run)

    writer = ZohoWriteClient(org, reader.tokens, audit or AuditLog(), http=http,
                             project=project.slug, plan_version=approved_version(project))  # fmt: skip
    for case in cases:
        run.cases.append(_run_case(writer, reader, name, case, url))
    run.passed = sum(c.passed for c in run.cases)
    run.failed = len(run.cases) - run.passed
    if run.failed and all(
        "not REST-enabled" in " ".join(c.problems) for c in run.cases if not c.passed
    ):
        run.setup_problem = (f"turn on REST API for {name} (Setup > Developer Hub > Functions > {name} > REST API), "
                             "or store its API-key URL: copilot deluge set-url")  # fmt: skip
    return _finish(project, run)


def _has_record_scope(org: OrgProfile, module: str) -> bool:
    s = set(org.scopes)
    return bool({f"ZohoCRM.modules.{module.lower()}.ALL", "ZohoCRM.modules.ALL"} & s)


def _run_case(
    writer: ZohoWriteClient, reader: ZohoReadClient, name: str, case, url: str | None
) -> CaseResult:
    res = CaseResult(name=case.name, passed=False)
    record_id = None
    try:
        if case.record:
            created = writer.create_test_record(case.module, case.record)
            if not created.ok:
                res.problems.append(
                    f"could not create test record: {created.get('code')}: {created.get('message')}"
                )
                return res
            record_id = str(created["details"]["id"])
        payload = writer.execute_function(name, _subst(case.input, record_id), api_key_url=url)
        status, code = payload.get("http_status"), str(payload.get("code", ""))
        if status and status >= 400 or code.lower() not in ("success", ""):
            msg = payload.get("message", "")
            hint = (
                " (function not REST-enabled or execute scope missing)"
                if status in (401, 403, 404)
                else ""
            )
            res.problems.append(
                f"execute failed: HTTP {status} {code}: {mask_contacts(str(msg))}{hint}"
            )
            if status in (401, 403, 404):
                res.problems.append("not REST-enabled")
            return res
        text, result = _parse_output(payload)
        res.output = mask_contacts(text[:MAX_OUTPUT]) if text else None
        if case.expected:
            if result is None:
                res.problems.append(
                    "function did not return a JSON result map (return result.toString())"
                )
            else:
                for k, v in case.expected.items():
                    if k not in result:
                        res.problems.append(f"result has no key {k!r}")
                    elif not _same(result[k], v):
                        res.problems.append(f"result[{k!r}] = {result[k]!r}, expected {v!r}")
        if case.expect_record and record_id:
            rec = (reader.get(f"/{case.module}/{record_id}", use_cache=False).get("data") or [{}])[
                0
            ]
            res.record_after = {
                k: rec.get(k) for k in case.expect_record
            }  # only the compared fields
            for k, v in case.expect_record.items():
                if not _same(rec.get(k), v):
                    res.problems.append(f"record.{k} = {rec.get(k)!r}, expected {v!r}")
        res.passed = not res.problems
        return res
    except (ZohoAPIError, httpx.HTTPError, KeyError) as exc:
        res.problems.append(f"{type(exc).__name__}: {exc}")
        return res
    finally:
        if record_id:
            deleted = writer.delete_test_record(case.module, record_id)
            if not deleted.ok:
                res.problems.append(
                    f"CLEANUP FAILED: delete {case.module} record {record_id} by hand"
                )
                res.passed = False


# ---------------------------------------------------------------- history + metric


def _history_path(project: Project, name: str) -> Path:
    return handoff.handoff_dir(project) / f"{name}.history.json"


def history(project: Project, name: str) -> list[dict]:
    p = _history_path(project, name)
    return json.loads(p.read_text("utf-8")) if p.exists() else []


def _finish(project: Project, run: TestRun) -> TestRun:
    folder = handoff.handoff_dir(project)
    (folder / f"{run.function}.testrun.json").write_text(
        run.model_dump_json(indent=2), encoding="utf-8"
    )
    hist = history(project, run.function)
    hist.append({"at": run.at.isoformat(), "code_sha": run.code_sha, "passed": run.passed,
                 "failed": run.failed, "setup_problem": run.setup_problem})  # fmt: skip
    _history_path(project, run.function).write_text(json.dumps(hist, indent=2), encoding="utf-8")
    return run


def function_metrics(project: Project) -> dict:
    """Per function: test runs, distinct code versions, and whether the first real run
    (ignoring setup problems) passed. Feeds 'functions passing on first placement'."""
    out = {}
    for p in handoff.handoff_dir(project).glob("*.history.json"):
        runs = [r for r in json.loads(p.read_text("utf-8")) if not r.get("setup_problem")]
        if not runs:
            continue
        name = p.name.removesuffix(".history.json")
        first_pass = runs[0]["failed"] == 0 and runs[0]["passed"] > 0
        out[name] = {"runs": len(runs), "code_versions": len({r["code_sha"] for r in runs}),
                     "passed_first_placement": first_pass,
                     "passing_now": runs[-1]["failed"] == 0 and runs[-1]["passed"] > 0}  # fmt: skip
    total = len(out)
    return {
        "functions": out,
        "first_placement_pass_rate": (
            sum(v["passed_first_placement"] for v in out.values()) / total
        )
        if total
        else None,
    }


# ---------------------------------------------------------------- interactive loop


def fix_loop(
    project: Project,
    name: str,
    confirm_push: Callable[[str], bool],
    push: Callable[[], dict],
    report: Callable[[TestRun], None],
    max_iterations: int = 5,
    wait_seconds: float = 600,
    poll: float = 2.0,
    http: httpx.Client | None = None,
) -> TestRun:
    """Run tests; on failure wait for the agent to save a fix (the .dg changes), show the diff,
    push the update only if the human confirms, run again. Stops on pass, timeout or limit."""
    tested_code = {code_sha(c): c for c in [handoff.load_function_code(project, name)]}
    run = run_tests(project, name, http=http)
    report(run)
    for _ in range(max_iterations - 1):
        if run.all_passed or (run.setup_problem and "not in the org" not in run.setup_problem):
            return run
        # Wait until the saved code differs from the code that was TESTED (content, not file
        # timestamps: a fix saved while the tests were still running must not be missed).
        before = tested_code.get(run.code_sha) or handoff.load_function_code(project, name)
        deadline = time.monotonic() + wait_seconds
        while code_sha(after := handoff.load_function_code(project, name)) == run.code_sha:
            if time.monotonic() > deadline:
                return run
            time.sleep(poll)
        if not confirm_push(_diff(before, after)):
            return run
        outcome = push()
        if not outcome.get("pushed"):
            return run
        tested_code[code_sha(after)] = after
        run = run_tests(project, name, http=http)
        report(run)
    return run


def _diff(a: str, b: str) -> str:
    import difflib

    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), "before", "after"))
