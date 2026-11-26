"""Typed-ish metadata readers. Each returns a trimmed, allowlisted view.

Why trim? (1) Fewer tokens for the LLM. (2) Allowlists keep unexpected PII out.
Endpoints are from the v8 docs (see docs/zoho-api-coverage.md, section 1).
"""

from __future__ import annotations

from copilot.privacy.masking import mask_users, pick
from copilot.zoho.client import ZohoReadClient

ORG_KEYS = ("id", "company_name", "country", "time_zone", "currency", "zgid", "type")
MODULE_KEYS = (
    "api_name",
    "module_name",
    "singular_label",
    "plural_label",
    "generated_type",
    "api_supported",
    "creatable",
    "editable",
    "deletable",
    "status",
    "id",
)
FIELD_KEYS = (
    "id",
    "api_name",
    "field_label",
    "data_type",
    "json_type",
    "length",
    "custom_field",
    "system_mandatory",
    "read_only",
    "visible",
    "decimal_place",
)
RELATED_LIST_KEYS = ("id", "api_name", "display_label", "type", "visible")
WORKFLOW_KEYS = ("id", "name", "description", "module", "status", "execute_when", "conditions")
BLUEPRINT_KEYS = (
    "id",
    "name",
    "module",
    "status",
    "layout",
    "process_info",
    "states",
    "transitions",
)
FUNCTION_KEYS = (
    "id",
    "api_name",
    "display_name",
    "description",
    "category",
    "language",
    "rest_api_mode",
    "status",
)
ROLE_KEYS = ("id", "name", "display_label", "share_with_peers", "description")
PROFILE_KEYS = ("id", "name", "display_label", "custom", "description", "default")


def get_org(c: ZohoReadClient) -> dict:
    """GET /org. Company-level settings only; contact fields (email, phone) are dropped."""
    orgs = c.get("/org").get("org", [])
    return pick(orgs[0], ORG_KEYS) if orgs else {}


LICENSE_KEYS = (
    "paid", "paid_type", "paid_expiry", "trial_type", "trial_expiry", "trial_action",
    "users_license_purchased", "license_type", "edition",
)  # fmt: skip


def org_edition_info(c: ZohoReadClient) -> dict:
    """Edition/licence facts only (no company name, emails or phones), plus the key names so
    we can see what Zoho actually returns for this kind of org."""
    orgs = c.get("/org", use_cache=False).get("org", [])
    if not orgs:
        return {}
    org = orgs[0]
    lic = org.get("license_details") if isinstance(org.get("license_details"), dict) else {}
    return {
        "type": org.get("type"),
        "license_details": pick(lic, LICENSE_KEYS),
        "license_keys": sorted(lic),
        "org_keys": sorted(org),
    }


def list_modules(c: ZohoReadClient) -> list[dict]:
    return [pick(m, MODULE_KEYS) for m in c.get("/settings/modules").get("modules", [])]


def _field_view(f: dict) -> dict:
    out = pick(f, FIELD_KEYS)
    if f.get("pick_list_values"):
        out["pick_list_values"] = [
            pick(p, ("display_value", "actual_value", "id")) for p in f["pick_list_values"]
        ]
    if isinstance(f.get("lookup"), dict) and f["lookup"].get("module"):
        out["lookup_module"] = f["lookup"]["module"].get("api_name")
    if isinstance(f.get("formula"), dict) and f["formula"].get("expression"):
        out["formula"] = pick(f["formula"], ("expression", "return_type"))
    return out


def get_fields(c: ZohoReadClient, module: str) -> list[dict]:
    body = c.get("/settings/fields", {"module": module})
    return [_field_view(f) for f in body.get("fields", [])]


def get_picklist_values(c: ZohoReadClient, module: str, field_id: str) -> list[dict]:
    body = c.get(f"/settings/fields/{field_id}/pick_list_values", {"module": module})
    return body.get("pick_list_values", [])


def get_layouts(c: ZohoReadClient, module: str) -> list[dict]:
    out = []
    for layout in c.get("/settings/layouts", {"module": module}).get("layouts", []):
        out.append(
            {
                **pick(layout, ("id", "name", "status")),
                "profiles": [p.get("name") for p in layout.get("profiles", [])],
                "sections": [
                    {
                        "label": s.get("display_label") or s.get("name"),
                        "fields": [
                            {"api_name": f.get("api_name"), "required": f.get("required", False)}
                            for f in s.get("fields", [])
                        ],
                    }
                    for s in layout.get("sections", [])
                ],
            }
        )
    return out


def get_related_lists(c: ZohoReadClient, module: str) -> list[dict]:
    body = c.get("/settings/related_lists", {"module": module})
    out = []
    for rl in body.get("related_lists", []):
        view = pick(rl, RELATED_LIST_KEYS)
        if isinstance(rl.get("module"), dict):
            view["module"] = rl["module"].get("api_name")
        out.append(view)
    return out


def list_workflow_rules(c: ZohoReadClient) -> list[dict]:
    body = c.get("/settings/automation/workflow_rules", {"per_page": 200})
    return [pick(w, WORKFLOW_KEYS) for w in body.get("workflow_rules", [])]


def list_blueprints(c: ZohoReadClient) -> list[dict]:
    return [pick(b, BLUEPRINT_KEYS) for b in c.get("/settings/blueprints").get("blueprints", [])]


def list_functions(c: ZohoReadClient) -> list[dict]:
    return [pick(f, FUNCTION_KEYS) for f in c.get("/settings/functions").get("functions", [])]


def list_roles(c: ZohoReadClient) -> list[dict]:
    out = []
    for r in c.get("/settings/roles").get("roles", []):
        view = pick(r, ROLE_KEYS)
        if isinstance(r.get("reporting_to"), dict):
            view["reporting_to"] = r["reporting_to"].get("name")
        out.append(view)
    return out


def list_profiles(c: ZohoReadClient) -> list[dict]:
    return [pick(p, PROFILE_KEYS) for p in c.get("/settings/profiles").get("profiles", [])]


def list_users_masked(c: ZohoReadClient) -> list[dict]:
    body = c.get("/users", {"type": "AllUsers", "per_page": 200})
    return mask_users(body.get("users", []))
