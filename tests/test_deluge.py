import json

import httpx
import pytest
import respx
from deluge_samples import BAD, GOOD

from copilot.deluge import docs, handoff
from copilot.deluge.checks import FieldCatalog, check_deluge
from copilot.planner.schema import FunctionItem

FN = FunctionItem(
    id="P-005", title="Post hot leads to Slack", rationale="r", sources=["k:1"],
    estimate_minutes=30, name="post_hot_lead", category="automation", purpose="post",
    uses_fields=["Leads.Last_Name", "Leads.Lead_Temperature"], connections=["slack_conn"],
)  # fmt: skip
CATALOG = FieldCatalog({"Leads": {"Last_Name", "Lead_Temperature", "Lead_Source"}})


def msgs(issues, level):
    return [f"{i.ref}: {i.message}" for i in issues if i.level == level]


# ---------------- static checks ----------------


def test_good_function_passes():
    issues = check_deluge(GOOD, FN, CATALOG)
    assert msgs(issues, "error") == []
    assert msgs(issues, "warning") == []


def test_bad_function_errors():
    errors = " | ".join(msgs(check_deluge(BAD, FN, CATALOG), "error"))
    assert "unclosed '{'" in errors
    assert "Authorization header" in errors
    assert "bearer token" in errors
    assert "invokeurl without a connection" in errors


def test_bad_function_warnings():
    warnings = " | ".join(msgs(check_deluge(BAD, FN, CATALOG), "warning"))
    assert "hard-coded record/org id" in warnings
    assert "boss@acme-realty.test" in warnings
    assert "'Lead_Temprature' is not a field of Leads" in warnings
    assert "never null-checked" in warnings
    assert "no info statements" in warnings


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (
            lambda c: c.replace("void automation.post_hot_lead", "void standalone.post_hot_lead"),
            "declared category",
        ),
        (lambda c: c.replace("post_hot_lead(", "post_lead("), "does not match"),
        (
            lambda c: c.replace('connection:"slack_conn"', 'connection:"teams_conn"'),
            "not declared on the plan item",
        ),
        (
            lambda c: c.replace('"Leads", lead_id', '"Leadz", lead_id'),
            "module 'Leadz' does not exist",
        ),
        (
            lambda c: c.replace(
                "result = Map();", 'token = "1000.' + "a" * 32 + "." + "b" * 32 + '";'
            ),
            "Zoho token",
        ),
        (
            lambda c: c.replace("result = Map();", 'api_key = "sk_live_abcdef";'),
            "hard-coded secret",
        ),
    ],
)
def test_check_catches(change, expected):
    errors = msgs(check_deluge(change(GOOD), FN, CATALOG), "error")
    assert any(expected in e for e in errors), errors


def test_strings_and_comments_do_not_break_bracket_check():
    code = GOOD.replace(
        "// Post a hot", '// a } stray brace in a comment\n\tx = "{ in a string";\n\t// Post'
    )
    assert msgs(check_deluge(code, FN, CATALOG), "error") == []


def test_wrong_return_type_and_uses_fields():
    fn = FN.model_copy(update={"uses_fields": ["Leads.Ghost"]})
    code = GOOD.replace("void automation", "map automation")
    errors = " | ".join(msgs(check_deluge(code, fn, CATALOG), "error"))
    assert "must return string or void" in errors
    assert "Leads.Ghost" in errors


def test_unavailable_metadata_is_a_warning():
    issues = check_deluge(GOOD, FN, FieldCatalog(available=False))
    assert msgs(issues, "error") == []
    assert any("NOT checked" in w for w in msgs(issues, "warning"))


# ---------------- header ----------------


def test_strip_header_roundtrip():
    text = handoff.HEADER_START + "\n  stuff\n" + handoff.HEADER_END + "\n" + GOOD
    assert handoff.strip_header(text) == GOOD


# ---------------- docs lookup ----------------

INTEGRATION = """<html><body>
<a href="/deluge/help/crm-tasks.html">Zoho CRM</a>
<a href="/deluge/help/books-tasks.html">Zoho Books</a>
<a href="https://evil.example/deluge/help/x.html">evil</a></body></html>"""
CRM_TASKS = """<html><body>
<a href="https://www.zoho.com/deluge/help/crm/get-record-by-id.html">Fetch Record using record ID</a>
<a href="https://www.zoho.com/deluge/help/crm/update-record.html">Update Record</a>
<a href="https://www.zoho.com/deluge/help/crm/search-records.html">Search Records</a></body></html>"""
PAGE = """<html><head><title>Get Record By ID | Help</title><script>var junk="AAAA";</script></head>
<body><nav>menu</nav><h1>Fetch Record</h1><p>Fetch a record using its ID.</p>
<pre>&lt;variable&gt; = zoho.crm.getRecordById(&lt;module&gt;, &lt;id&gt;);</pre>
<table><tr><td>module</td><td>TEXT</td></tr></table></body></html>"""


@pytest.fixture
def fetcher(tmp_path):
    return docs.DocFetcher(http=httpx.Client(), cache_dir=tmp_path / "cache")


@respx.mock
def test_search_ranks_app_tasks_locally(fetcher):
    respx.get(docs.INTEGRATION_INDEX).respond(200, text=INTEGRATION)
    respx.get("https://www.zoho.com/deluge/help/crm-tasks.html").respond(200, text=CRM_TASKS)
    respx.get("https://www.zoho.com/deluge/help/crm-integration-tasks-V8.html").respond(404)
    hits = docs.search("get record by id", "crm", fetcher=fetcher)
    assert hits[0].url.endswith("crm/get-record-by-id.html")
    # the query itself is never sent anywhere: only these three index GETs happened
    assert {str(c.request.url) for c in respx.calls} <= {
        docs.INTEGRATION_INDEX,
        "https://www.zoho.com/deluge/help/crm-tasks.html",
        "https://www.zoho.com/deluge/help/crm-integration-tasks-V8.html",
    }
    assert all("record" not in str(c.request.url.query) for c in respx.calls)


@respx.mock
def test_core_pages_found_for_http_calls(fetcher):
    respx.get(docs.INTEGRATION_INDEX).respond(200, text=INTEGRATION)
    respx.get(url__startswith="https://www.zoho.com/deluge/help/crm").respond(
        200, text="<html></html>"
    )
    hits = docs.search("send an http request to an external api", "crm", fetcher=fetcher)
    assert hits[0].url.endswith("web-data/invokeurl-task.html")


@respx.mock
def test_read_doc_extracts_text_and_code_and_caches(fetcher):
    url = "https://www.zoho.com/deluge/help/crm/get-record-by-id.html"
    route = respx.get(url).respond(200, text=PAGE)
    d = docs.read_doc(url + "?utm=x#frag", fetcher=fetcher)
    assert d["title"] == "Get Record By ID | Help"
    assert "zoho.crm.getRecordById(<module>, <id>);" in d["text"]
    assert "```" in d["text"] and "| module" in d["text"]
    assert "junk" not in d["text"] and "menu" not in d["text"]
    docs.read_doc(url, fetcher=fetcher)
    assert route.call_count == 1  # cached


@pytest.mark.parametrize(
    "url",
    [
        "http://www.zoho.com/deluge/help/x.html",
        "https://evil.example/deluge/help/x.html",
        "https://www.zoho.com.evil.io/deluge/help/x.html",
        "https://www.zoho.com/r/careers.html",
        "https://accounts.zoho.com/oauth/v2/token",
    ],
)
def test_doc_allowlist(url, fetcher):
    with pytest.raises(docs.DocAccessError):
        fetcher.get(url)


@respx.mock
def test_redirect_outside_allowlist_is_refused(fetcher):
    respx.get("https://www.zoho.com/deluge/help/old.html").respond(
        301, headers={"Location": "https://www.zoho.com/r/elsewhere.html"}
    )
    respx.get("https://www.zoho.com/r/elsewhere.html").respond(200, text="x")
    with pytest.raises(docs.DocAccessError):
        fetcher.get("https://www.zoho.com/deluge/help/old.html")


def test_testcase_model():
    t = handoff.TestCase(name="hot", input={"Lead_Temperature": "Hot"}, expected={"status": "ok"})
    assert json.loads(t.model_dump_json())["expected"] == {"status": "ok"}
