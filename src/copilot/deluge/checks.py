"""Static checks for generated Deluge, run locally before anything reaches Zoho.

Deluge has no public parser, so these are deliberate, conservative heuristics:
errors for things that are certainly wrong or dangerous, warnings for likely problems.
Zoho's own compiler (COMPILATION_ERROR with line/position on push) is the final judge
of syntax.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from copilot.planner.schema import FunctionItem, Issue

# Return types per category (create-function docs, v8)
CATEGORY_RETURN = {
    "automation": {"void", "string"},
    "standalone": {"string"},
    "button": {"string"},
    "schedule": {"void"},
    "related_list": {"string"},
    "validation": {"map"},
}
# Declaration keyword per plan category. Only "standalone" is confirmed by a Zoho sample;
# the others follow the same pattern and are confirmed by Zoho's compiler on push.
DECL_KEYWORD = {
    "automation": "automation",
    "standalone": "standalone",
    "button": "button",
    "schedule": "schedule",
    "related_list": "relatedlist",
    "validation": "validationrule",
}

DECL_RE = re.compile(r"^\s*(\w+)\s+([a-z_]+)\.([a-z][a-z0-9_]*)\s*\((.*?)\)\s*\{", re.M | re.S)
INVOKEURL_RE = re.compile(r"invokeurl\s*\[(.*?)\]", re.S | re.I)
CONNECTION_PARAM_RE = re.compile(r"connection\s*:\s*\"([^\"]*)\"", re.I)
CRM_TASK_RE = re.compile(r"zoho\.crm\.(\w+)\s*\(\s*\"([A-Za-z0-9_]+)\"", re.S)
CRM_CONN_RE = re.compile(r"zoho\.crm\.\w+\s*\((?:[^;]*?),\s*\"([a-z][a-z0-9_]*)\"\s*\)\s*;", re.S)
GET_RE = re.compile(r"\.get\s*\(\s*\"([A-Za-z0-9_]+)\"\s*\)")
PUT_RE = re.compile(r"\.put\s*\(\s*\"([A-Za-z0-9_]+)\"\s*,")
ASSIGN_FROM_GET_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*[^;]*?\.get\s*\(", re.M)
ASSIGN_FROM_FETCH_RE = re.compile(
    r"^\s*([A-Za-z_]\w*)\s*=\s*zoho\.crm\.(getRecordById|getRecords|searchRecords)\b", re.M
)
LONG_ID_RE = re.compile(r"(?<![\w.])\d{15,}(?![\w.])")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

SECRET_PATTERNS = [
    (re.compile(r"Zoho-oauthtoken\s+\S", re.I), "hard-coded Zoho OAuth token"),
    (re.compile(r"\b(authtoken|zapikey|api[_-]?key|client[_-]?secret|password|secret)\b\s*[=:]\s*\"[^\"]{6,}\"", re.I), "hard-coded secret"),
    (re.compile(r"\"Authorization\"\s*:", re.I), "Authorization header set by hand; use a connection"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._-]{12,}"), "hard-coded bearer token"),
    (re.compile(r"\b1000\.[0-9a-f]{32}\.[0-9a-f]{32}\b"), "Zoho token or secret literal"),
]  # fmt: skip


@dataclass
class FieldCatalog:
    """Module -> field api_names, from the live org plus fields the plan creates."""

    fields_by_module: dict[str, set[str]] = field(default_factory=dict)
    available: bool = True


def _strip_comments_and_strings(code: str) -> str:
    """Blank out comments and string contents (keeps positions/line numbers)."""
    out, i, n = [], 0, len(code)
    while i < n:
        c = code[i]
        if code.startswith("//", i):
            j = code.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        elif code.startswith("/*", i):
            j = code.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(re.sub(r"[^\n]", " ", code[i:j]))
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and code[j] != c:
                j += 2 if code[j] == "\\" else 1
            out.append(c + re.sub(r"[^\n]", " ", code[i + 1 : j]) + (c if j < n else ""))
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _line_of(code: str, pos: int) -> int:
    return code.count("\n", 0, pos) + 1


def check_deluge(code: str, fn: FunctionItem, catalog: FieldCatalog) -> list[Issue]:
    issues: list[Issue] = []

    def err(msg: str, line: int | None = None) -> None:
        issues.append(Issue(level="error", ref=f"line {line}" if line else fn.name, message=msg))

    def warn(msg: str, line: int | None = None) -> None:
        issues.append(Issue(level="warning", ref=f"line {line}" if line else fn.name, message=msg))

    bare = _strip_comments_and_strings(code)

    # 1. declaration: <returntype> <category>.<name>(args) {
    m = DECL_RE.search(code)
    if not m:
        err("missing function declaration like: void automation.my_function(String id) {")
    else:
        ret, cat_kw, name, _args = m.groups()
        if name != fn.name:
            err(f"declared name {name!r} does not match the plan's function name {fn.name!r}")
        expected_kw = DECL_KEYWORD[fn.category]
        if cat_kw != expected_kw:
            err(
                f"declared category {cat_kw!r}, plan says {fn.category!r} (expected {expected_kw!r})"
            )
        allowed = CATEGORY_RETURN[fn.category]
        if ret.lower() not in allowed:
            err(f"{fn.category} functions must return {' or '.join(sorted(allowed))}, not {ret!r}")
        for arg in fn.arguments:
            if not re.search(rf"\b{re.escape(arg)}\b", _args):
                warn(f"plan argument {arg!r} is not in the declaration")

    # 2. balanced brackets (outside strings/comments)
    pairs, stack = {")": "(", "]": "[", "}": "{"}, []
    for pos, ch in enumerate(bare):
        if ch in "([{":
            stack.append((ch, pos))
        elif ch in pairs:
            if not stack or stack[-1][0] != pairs[ch]:
                err(f"unbalanced {ch!r}", _line_of(code, pos))
                break
            stack.pop()
    else:
        if stack:
            err(f"unclosed {stack[-1][0]!r}", _line_of(code, stack[-1][1]))

    # 3. secrets and hard-coded environment data
    for pattern, label in SECRET_PATTERNS:
        for hit in pattern.finditer(code):
            err(f"{label}; never put credentials in code", _line_of(code, hit.start()))
    for hit in LONG_ID_RE.finditer(code):
        warn(
            "hard-coded record/org id: ids differ between sandbox and production",
            _line_of(code, hit.start()),
        )
    for hit in EMAIL_RE.finditer(code):
        warn(
            f"hard-coded email address {hit.group(0)!r}: use a field, user lookup or org variable",
            _line_of(code, hit.start()),
        )

    # 4. external calls must use a connection the plan declares
    declared = set(fn.connections)
    for block in INVOKEURL_RE.finditer(code):
        line = _line_of(code, block.start())
        conn = CONNECTION_PARAM_RE.search(block.group(1))
        if not conn:
            err(
                "invokeurl without a connection: parameter (use a connection, not manual auth)",
                line,
            )
        elif conn.group(1) not in declared:
            err(
                f"connection {conn.group(1)!r} is not declared on the plan item ({sorted(declared) or 'none'})",
                line,
            )
    for hit in CRM_CONN_RE.finditer(code):
        if hit.group(1) not in declared:
            warn(
                f"zoho.crm call uses connection {hit.group(1)!r} not declared on the plan item",
                _line_of(code, hit.start()),
            )

    # 5. module and field api names against the org + plan
    modules_used = {mod for _, mod in CRM_TASK_RE.findall(code)}
    modules_used |= {ref.split(".")[0] for ref in fn.uses_fields if "." in ref}
    if catalog.available:
        for task in CRM_TASK_RE.finditer(code):
            mod = task.group(2)
            if mod not in catalog.fields_by_module:
                err(
                    f"module {mod!r} does not exist in the org or the plan",
                    _line_of(code, task.start()),
                )
        known = set().union(*(catalog.fields_by_module.get(m, set()) for m in modules_used)) | {
            "id"
        }
        if modules_used:
            for rx in (GET_RE, PUT_RE):
                for hit in rx.finditer(code):
                    name = hit.group(1)
                    if name not in known and not name.islower():
                        warn(
                            f"{name!r} is not a field of {', '.join(sorted(modules_used))} "
                            "(fine if it's a plain map key)",
                            _line_of(code, hit.start()),
                        )
        for ref in fn.uses_fields:
            if "." in ref:
                mod, fld = ref.split(".", 1)
                if fld not in catalog.fields_by_module.get(mod, set()) and fld != "id":
                    err(f"plan field {ref!r} does not exist in the org or the plan")
    else:
        warn("org metadata unavailable: module/field names were NOT checked")

    # 6. null handling for values read from records
    has_null_check = re.search(
        r"isNull\s*\(|isEmpty\s*\(|ifnull\s*\(|!=\s*null|==\s*null", bare, re.I
    )
    risky = ASSIGN_FROM_GET_RE.findall(bare) + [v for v, _ in ASSIGN_FROM_FETCH_RE.findall(bare)]
    if risky and not has_null_check:
        warn(
            f"values read from records ({', '.join(sorted(set(risky))[:5])}) are never null-checked"
        )

    # 7. house conventions that make failures diagnosable
    if not re.search(r"\binfo\b", bare):
        warn("no info statements: add `info` logs so failures can be diagnosed")
    if fn.category in ("standalone", "button", "validation") and not re.search(r"\breturn\b", bare):
        err(f"{fn.category} functions must return a value")
    if fn.category == "standalone" and '"status"' not in code:
        warn('standalone functions should return a result map with a "status" key (as a string)')
    return issues
