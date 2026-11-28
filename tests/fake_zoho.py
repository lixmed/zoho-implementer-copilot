"""A fake Zoho (accounts + CRM v8) for end-to-end tests. All data is invented.

Response shapes follow the v8 docs. They are NOT recorded from a real org yet. Once
the dev org is connected we'll record real responses and diff them against these.
"""

from __future__ import annotations

import httpx
import respx

ACCOUNTS = "https://accounts.zoho.com"
DEV_API = "https://developer.zohoapis.com"

ORG = {
    "org": [
        {
            "id": "900001",
            "company_name": "Demo Realty (fake)",
            "primary_email": "owner@demo-realty.test",
            "phone": "+1 555 0000",
            "country": "United States",
            "time_zone": "America/New_York",
            "currency": "USD",
            "type": "developer",
            # shape recorded from a real free org (values changed)
            "license_details": {
                "paid": False,
                "paid_type": "free",
                "paid_expiry": None,
                "trial_type": "enterprise",
                "trial_expiry": "2026-12-10T02:00:00+02:00",
                "users_license_purchased": 3,
                "portal_users_license_purchased": 0,
            },
        }
    ]
}

MODULES = {
    "modules": [
        {
            "api_name": "Leads",
            "module_name": "Leads",
            "singular_label": "Lead",
            "plural_label": "Leads",
            "generated_type": "default",
            "api_supported": True,
            "creatable": True,
            "id": "m1",
            "profiles": [{"name": "Administrator"}],
        },
        {
            "api_name": "Deals",
            "module_name": "Deals",
            "singular_label": "Deal",
            "plural_label": "Deals",
            "generated_type": "default",
            "api_supported": True,
            "creatable": True,
            "id": "m2",
        },
        {
            "api_name": "Properties",
            "module_name": "Properties",
            "singular_label": "Property",
            "plural_label": "Properties",
            "generated_type": "custom",
            "api_supported": True,
            "creatable": True,
            "id": "m3",
        },
        {
            "api_name": "Visits",
            "module_name": "Visits",
            "generated_type": "web",
            "api_supported": False,
            "id": "m4",
        },
    ]
}

FIELDS = {
    "Leads": {
        "fields": [
            {
                "id": "f1",
                "api_name": "Last_Name",
                "field_label": "Last Name",
                "data_type": "text",
                "length": 80,
                "custom_field": False,
                "system_mandatory": True,
            },
            {
                "id": "f2",
                "api_name": "Lead_Source",
                "field_label": "Lead Source",
                "data_type": "picklist",
                "custom_field": False,
                "pick_list_values": [
                    {"display_value": "Website", "actual_value": "Website", "id": "p1"},
                    {"display_value": "Referral", "actual_value": "Referral", "id": "p2"},
                ],
            },
            {
                "id": "f3",
                "api_name": "Budget_Range",
                "field_label": "Budget Range",
                "data_type": "picklist",
                "custom_field": True,
                "pick_list_values": [{"display_value": "<200k", "actual_value": "<200k"}],
            },
        ]
    },
    "Deals": {
        "fields": [
            {
                "id": "f10",
                "api_name": "Deal_Name",
                "field_label": "Deal Name",
                "data_type": "text",
                "custom_field": False,
                "system_mandatory": True,
            },
            {
                "id": "f11",
                "api_name": "Property",
                "field_label": "Property",
                "data_type": "lookup",
                "custom_field": True,
                "lookup": {"module": {"api_name": "Properties", "id": "m3"}},
            },
        ]
    },
    "Properties": {
        "fields": [
            {
                "id": "f20",
                "api_name": "Name",
                "field_label": "Property Name",
                "data_type": "text",
                "custom_field": False,
            },
            {
                "id": "f21",
                "api_name": "Asking_Price",
                "field_label": "Asking Price",
                "data_type": "currency",
                "custom_field": True,
                "decimal_place": 2,
            },
        ]
    },
}

LAYOUTS = {
    "layouts": [
        {
            "id": "l1",
            "name": "Standard",
            "status": "active",
            "profiles": [{"name": "Administrator", "id": "pr1"}],
            "sections": [
                {
                    "display_label": "Lead Information",
                    "fields": [
                        {"api_name": "Last_Name", "required": True},
                        {"api_name": "Lead_Source"},
                    ],
                }
            ],
        }
    ]
}

WORKFLOWS = {
    "workflow_rules": [
        {
            "id": "w1",
            "name": "Hot lead alert",
            "module": {"api_name": "Leads"},
            "status": {"active": True},
            "execute_when": {"type": "create"},
            "created_by": {"name": "Jane Fake", "email": "jane@demo-realty.test"},
        }
    ]
}

FUNCTIONS = {
    "functions": [
        {
            "id": "fn1",
            "api_name": "assign_agent",
            "display_name": "Assign agent",
            "category": "automation",
            "language": "deluge",
            "rest_api_mode": "None",
        }
    ]
}

ROLES = {
    "roles": [
        {"id": "r1", "name": "CEO"},
        {"id": "r2", "name": "Agent", "reporting_to": {"id": "r1", "name": "CEO"}},
    ]
}
PROFILES = {
    "profiles": [
        {"id": "pr1", "name": "Administrator", "custom": False},
        {"id": "pr2", "name": "Agent", "custom": True},
    ]
}


USERS = {
    "users": [
        {"id": "u1", "full_name": "Jane Fake", "email": "jane@demo-realty.test",
         "phone": "+1 555 0101", "status": "active", "confirm": True,
         "role": {"id": "r1", "name": "CEO"}, "profile": {"id": "pr1", "name": "Administrator"}},
        {"id": "u2", "full_name": "Omar Fake", "email": "omar@demo-realty.test",
         "status": "active", "role": {"id": "r2", "name": "Agent"},
         "profile": {"id": "pr2", "name": "Agent"}},
    ]
}  # fmt: skip


def token_body(api_domain: str = DEV_API, refresh: bool = True) -> dict:
    body = {
        "access_token": "fake-access",
        "api_domain": api_domain,
        "expires_in": 3600,
        "token_type": "Bearer",
    }
    if refresh:
        body["refresh_token"] = "fake-refresh"
    return body


def mount(router: respx.Router, api: str = DEV_API) -> None:
    """Register every CRM metadata route on a respx router."""
    base = f"{api}/crm/v8"
    router.get(f"{base}/org").respond(200, json=ORG)
    router.get(f"{base}/settings/modules").respond(
        200, json=MODULES, headers={"X-API-CREDITS-REMAINING": "4990"}
    )
    for module, body in FIELDS.items():
        router.get(f"{base}/settings/fields", params={"module": module}).respond(200, json=body)
    router.get(f"{base}/settings/layouts").respond(200, json=LAYOUTS)
    router.get(f"{base}/settings/related_lists").respond(204)
    router.get(f"{base}/settings/automation/workflow_rules").respond(200, json=WORKFLOWS)
    router.get(f"{base}/settings/blueprints").respond(204)
    router.get(f"{base}/settings/functions").respond(200, json=FUNCTIONS)
    router.get(f"{base}/settings/roles").respond(200, json=ROLES)
    router.get(f"{base}/settings/profiles").respond(200, json=PROFILES)
    router.get(f"{base}/users").respond(200, json=USERS)

    def picklist(request):
        fid = request.url.path.split("/settings/fields/")[1].split("/")[0]
        for body in FIELDS.values():
            for f in body["fields"]:
                if f["id"] == fid:
                    vals = [
                        {"id": f"{fid}-{n}", **v}
                        for n, v in enumerate(f.get("pick_list_values", []))
                    ]
                    return httpx.Response(200, json={"pick_list_values": vals})
        return httpx.Response(204)

    router.get(url__regex=rf"{base}/settings/fields/\w+/pick_list_values").mock(
        side_effect=picklist
    )
