"""A stateful fake of the Zoho CRM settings APIs the Builder uses (fields, layouts, actions,
workflow rules, modules). Writes change the state; later reads see the change.

Request/response shapes follow the v8 doc samples. Invented data only.
"""

from __future__ import annotations

import copy
import itertools
import json
import re

import httpx

ACCOUNTS = "https://accounts.zoho.com"
API_DOMAIN = "https://developer.zohoapis.com"
API = f"{API_DOMAIN}/crm/v8"


class FakeOrg:
    def __init__(self):
        self.ids = itertools.count(9000)
        self.modules = {"Leads": {"api_name": "Leads", "id": "m1", "api_supported": True},
                        "Deals": {"api_name": "Deals", "id": "m2", "api_supported": True}}  # fmt: skip
        self.fields = {
            "Leads": {
                "Last_Name": {"id": "f1", "api_name": "Last_Name", "field_label": "Last Name", "data_type": "text"},
                "Lead_Source": {"id": "f2", "api_name": "Lead_Source", "field_label": "Lead Source", "data_type": "picklist"},
            },
            "Deals": {
                "Deal_Name": {"id": "f10", "api_name": "Deal_Name", "field_label": "Deal Name", "data_type": "text"},
                "Property": {"id": "f11", "api_name": "Property", "field_label": "Property", "data_type": "lookup"},
            },
        }  # fmt: skip
        self.picklists = {"f2": [{"id": "p1", "display_value": "Website", "actual_value": "Website"},
                                 {"id": "p2", "display_value": "Referral", "actual_value": "Referral"}]}  # fmt: skip
        self.unused: dict[str, set[str]] = {}
        self.layouts = {
            "Leads": [{"id": "l1", "name": "Standard", "status": "active", "sections": [
                {"id": "s1", "display_label": "Lead Information",
                 "fields": [{"id": "f1", "api_name": "Last_Name", "required": True},
                            {"id": "f2", "api_name": "Lead_Source", "required": False}]}]}],
            "Deals": [{"id": "l2", "name": "Standard", "status": "active", "sections": [
                {"id": "s2", "display_label": "Deal Information",
                 "fields": [{"id": "f10", "api_name": "Deal_Name", "required": True}]},
                {"id": "s3", "display_label": "Other", "fields": [{"id": "f11", "api_name": "Property", "required": False}]}]}],
        }  # fmt: skip
        self.workflows: dict[str, dict] = {}
        self.actions: dict[str, dict] = {}
        self.profiles = [{"id": "pr1", "name": "Administrator"}]
        self.fail_on: str | None = None  # e.g. "POST workflow_rules" to inject a failure
        self.writes: list[str] = []

    def new_id(self) -> str:
        return str(next(self.ids))

    # ------------------------------------------------------------ helpers

    def _field_by_id(self, fid):
        for mod, fs in self.fields.items():
            for f in fs.values():
                if f["id"] == fid:
                    return mod, f
        return None, None

    def _remove_from_layouts(self, module, fid):
        for lay in self.layouts.get(module, []):
            for s in lay["sections"]:
                s["fields"] = [f for f in s["fields"] if f["id"] != fid]

    # ------------------------------------------------------------ handlers

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/crm/v8", 1)[1]
        method = request.method
        module = request.url.params.get("module")
        body = json.loads(request.content) if request.content else {}
        if method != "GET":
            key = f"{method} {path}"
            self.writes.append(key)
            if self.fail_on and self.fail_on in key:
                return httpx.Response(
                    400,
                    json={"code": "INVALID_DATA", "message": "injected failure", "status": "error"},
                )

        if method == "GET" and path == "/settings/modules":
            return httpx.Response(200, json={"modules": list(self.modules.values())})
        if method == "GET" and path == "/settings/fields":
            return httpx.Response(
                200, json={"fields": copy.deepcopy(list(self.fields.get(module, {}).values()))}
            )
        if m := re.fullmatch(r"/settings/fields/(\w+)/pick_list_values", path):
            return httpx.Response(
                200, json={"pick_list_values": copy.deepcopy(self.picklists.get(m.group(1), []))}
            )
        if method == "GET" and path == "/settings/layouts":
            return httpx.Response(
                200, json={"layouts": copy.deepcopy(self.layouts.get(module, []))}
            )
        if method == "GET" and path == "/settings/automation/workflow_rules":
            return httpx.Response(
                200, json={"workflow_rules": copy.deepcopy(list(self.workflows.values()))}
            )
        if method == "GET" and path == "/settings/profiles":
            return httpx.Response(200, json={"profiles": self.profiles})

        if method == "POST" and path == "/settings/modules":
            mod = body["modules"][0]
            self.modules[mod["api_name"]] = {
                "api_name": mod["api_name"],
                "id": self.new_id(),
                "api_supported": True,
            }
            self.fields[mod["api_name"]] = {}
            return self.ok("modules", self.modules[mod["api_name"]]["id"])
        if method == "POST" and path == "/settings/fields":
            results = []
            for f in body["fields"]:
                fid = self.new_id()
                api = re.sub(r"\W+", "_", f["field_label"]).strip("_")
                self.fields[module][api] = {
                    "id": fid,
                    "api_name": api,
                    **{k: v for k, v in f.items() if k != "layout_associations"},
                }
                if f.get("pick_list_values"):
                    self.picklists[fid] = [
                        {"id": self.new_id(), **v} for v in f["pick_list_values"]
                    ]
                targets = {a["id"] for a in f.get("layout_associations", [])} or {
                    self.layouts[module][0]["id"]
                }
                for lay in self.layouts.get(module, []):
                    if lay["id"] in targets:
                        lay["sections"][0]["fields"].append(
                            {"id": fid, "api_name": api, "required": False}
                        )
                results.append(
                    {
                        "code": "SUCCESS",
                        "details": {"id": fid},
                        "message": "field created",
                        "status": "success",
                    }
                )
            return httpx.Response(201, json={"fields": results})
        if (m := re.fullmatch(r"/settings/fields/(\w+)", path)) and method == "PATCH":
            fid = m.group(1)
            for v in body["fields"][0].get("pick_list_values", []):
                existing = next(
                    (p for p in self.picklists.setdefault(fid, []) if p["id"] == v.get("id")), None
                )
                if existing:
                    existing["display_value"] = v["display_value"]
                else:
                    self.picklists[fid].append({"id": self.new_id(), "display_value": v["display_value"],
                                                "actual_value": v.get("actual_value", v["display_value"])})  # fmt: skip
            return self.ok("fields", fid)
        if (m := re.fullmatch(r"/settings/fields/(\w+)", path)) and method == "DELETE":
            mod, f = self._field_by_id(m.group(1))
            del self.fields[mod][f["api_name"]]
            self._remove_from_layouts(mod, f["id"])
            return self.ok("fields", f["id"])
        if (m := re.fullmatch(r"/settings/layouts/(\w+)", path)) and method == "PATCH":
            lay = next(x for x in self.layouts[module] if x["id"] == m.group(1))
            for s_in in body["layouts"][0]["sections"]:
                target = next(s for s in lay["sections"] if s["id"] == s_in["id"])
                for f_in in s_in["fields"]:
                    fid = f_in["id"]
                    if f_in.get("_delete"):
                        self._remove_from_layouts(module, fid)
                        continue
                    if "pick_list_values" in f_in:
                        keep = {v["id"] for v in f_in["pick_list_values"]}
                        self.unused[fid] = {p["id"] for p in self.picklists.get(fid, [])} - keep
                        continue
                    _, f = self._field_by_id(fid)
                    self._remove_from_layouts(module, fid)
                    target["fields"].append(
                        {
                            "id": fid,
                            "api_name": f["api_name"],
                            "required": f_in.get("required", False),
                        }
                    )
            return self.ok("layouts", lay["id"])
        if method == "POST" and path in (
            "/settings/automation/field_updates",
            "/settings/automation/tasks",
        ):
            key = "field_updates" if "field_updates" in path else "tasks"
            aid = self.new_id()
            self.actions[aid] = {**body[key][0], "kind": key}
            return self.ok(key, aid)
        if (
            m := re.fullmatch(r"/settings/automation/(field_updates|tasks)/(\w+)", path)
        ) and method == "DELETE":
            self.actions.pop(m.group(2))
            return self.ok(m.group(1), m.group(2))
        if method == "POST" and path == "/settings/automation/workflow_rules":
            rid = self.new_id()
            rule = body["workflow_rules"][0]
            for a in rule["conditions"][0]["instant_actions"]["actions"]:
                assert a["id"] in self.actions, f"rule references unknown action {a['id']}"
            self.workflows[rid] = {"id": rid, **rule, "status": {"active": True}}
            return self.ok("workflow_rules", rid)
        if (
            m := re.fullmatch(r"/settings/automation/workflow_rules/(\w+)", path)
        ) and method == "PUT":
            self.workflows[m.group(1)]["status"] = body["workflow_rules"][0]["status"]
            return self.ok("workflow_rules", m.group(1))
        if (
            m := re.fullmatch(r"/settings/automation/workflow_rules/(\w+)", path)
        ) and method == "DELETE":
            self.workflows.pop(m.group(1))
            return self.ok("workflow_rules", m.group(1))
        return httpx.Response(
            404, json={"code": "INVALID_URL_PATTERN", "message": f"fake: {method} {path}"}
        )

    @staticmethod
    def ok(key, rid):
        return httpx.Response(
            200,
            json={
                key: [
                    {
                        "code": "SUCCESS",
                        "details": {"id": rid},
                        "message": "ok",
                        "status": "success",
                    }
                ]
            },
        )

    def transport(self) -> httpx.MockTransport:
        """An httpx transport answering the OAuth token endpoint and the CRM settings APIs, so
        the real clients can run fully offline (used by `copilot demo`)."""

        def route(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith(f"{ACCOUNTS}/oauth/v2/token"):
                return httpx.Response(200, json={"access_token": "demo-access", "api_domain": API_DOMAIN,
                                                 "expires_in": 3600, "token_type": "Bearer"})  # fmt: skip
            if url.startswith(API):
                return self.handle(request)
            return httpx.Response(
                404, json={"code": "DEMO_OFFLINE", "message": f"no network in demo: {url}"}
            )

        return httpx.MockTransport(route)

    # convenience
    def layout_field(self, module, api_name):
        for lay in self.layouts[module]:
            for s in lay["sections"]:
                for f in s["fields"]:
                    if f["api_name"] == api_name:
                        return s["display_label"], f["required"]
        return None
