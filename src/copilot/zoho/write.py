"""The ONLY code that writes to Zoho. Kept separate from the read client on purpose.

- Refuses production orgs outright (production safeguards arrive in Phase 7).
- Every call, success or failure, is written to the audit log with before/after.
- Phase 3: create/update a Deluge function. Attaching a function to a workflow is NOT
  implemented (no verified API, needs approval).
- Phase 4: modules, fields, layouts, field-update/task actions and workflow rules, each with
  the request shape taken from Zoho's v8 doc samples (see docs/builder.md).
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from copilot.audit.log import AuditLog
from copilot.orgs.registry import OrgProfile
from copilot.zoho.client import API_VERSION, TokenSource

FUNCTION_WRITE_SCOPES = ("ZohoCRM.settings.functions.CREATE", "ZohoCRM.settings.functions.UPDATE")
BUILDER_WRITE_SCOPES = (
    "ZohoCRM.settings.modules.CREATE",
    "ZohoCRM.settings.fields.CREATE",
    "ZohoCRM.settings.fields.UPDATE",
    "ZohoCRM.settings.fields.DELETE",  # only used by `rollback --hard`
    "ZohoCRM.settings.layouts.UPDATE",
    "ZohoCRM.settings.automation_actions.CREATE",
    "ZohoCRM.settings.automation_actions.DELETE",
    "ZohoCRM.settings.workflow_rules.CREATE",
    "ZohoCRM.settings.workflow_rules.UPDATE",
    "ZohoCRM.settings.workflow_rules.DELETE",
)
_RESULT_KEYS = (
    "functions", "data", "fields", "layouts", "modules", "workflow_rules", "field_updates", "tasks",
)  # fmt: skip


class WriteRefusedError(PermissionError):
    pass


class ZohoWriteResult(dict):
    @property
    def ok(self) -> bool:
        return self.get("status") == "success"


def _all_results(body: Any) -> list[dict]:
    """Zoho wraps results as {"<entity>": [ {...}, ... ]}, or returns a flat error object."""
    if isinstance(body, dict):
        for key in _RESULT_KEYS:
            if isinstance(body.get(key), list) and body[key]:
                return [r for r in body[key] if isinstance(r, dict)]
        return [body]
    return [{}]


def _first_result(body: Any) -> dict:
    return _all_results(body)[0]


class ZohoWriteClient:
    def __init__(
        self,
        org: OrgProfile,
        tokens: TokenSource,
        audit: AuditLog,
        http: httpx.Client | None = None,
        project: str | None = None,
        plan_version: int | None = None,
    ):
        if org.environment == "production":
            raise WriteRefusedError("writes to production orgs are blocked until Phase 7")
        self.org, self._tokens, self._audit = org, tokens, audit
        self._http = http or httpx.Client(timeout=60.0)
        self._project, self._plan_version = project, plan_version
        self.base = f"{org.api_domain.rstrip('/')}/crm/{API_VERSION}"

    def _send(
        self, method: str, path: str, metadata: dict, code_file: tuple[str, str] | None = None
    ) -> tuple[int, dict]:
        files = {"metadata": ("function.json", json.dumps(metadata), "application/json")}
        if code_file:  # Deluge source as a separate .ds part instead of inline "_code"
            files["code"] = (code_file[0], code_file[1], "application/octet-stream")
        resp = self._http.request(
            method,
            f"{self.base}{path}",
            files=files,
            headers={"Authorization": f"Zoho-oauthtoken {self._tokens.get()}"},
        )
        try:
            body = resp.json()
        except ValueError:
            body = {"code": "HTTP_ERROR", "message": resp.text[:300]}
        return resp.status_code, _first_result(body)

    def _audited(
        self,
        action: str,
        target: str,
        method: str,
        path: str,
        metadata: dict,
        before,
        code_file: tuple[str, str] | None = None,
    ):
        status, result = self._send(method, path, metadata, code_file)
        ok = result.get("status") == "success" and status < 300
        fn_meta = metadata["functions"][0]
        code_len = len(code_file[1]) if code_file else len(fn_meta.get("_code", ""))
        safe_meta = {
            "functions": [
                {k: v for k, v in fn_meta.items() if k != "_code"} | {"code_chars": code_len}
            ],
            "code_sent_as": "file" if code_file else "_code",
            "http_status": status,
        }
        self._audit.record(
            org=self.org.slug,
            environment=self.org.environment,
            action=action,
            target=target,
            result="success" if ok else f"error:{result.get('code', status)}",
            before=before,
            after={"request": safe_meta, "response": result},
            project=self._project,
            plan_version=self._plan_version,
        )
        return ZohoWriteResult(
            status="success" if ok else "error",
            http_status=status,
            code=result.get("code"),
            message=result.get("message"),
            details=result.get("details"),
        )

    def create_function(
        self, api_name: str, category: str, code: str, description: str, code_as_file: bool = False
    ) -> ZohoWriteResult:
        fn = {
            "name": api_name,
            "api_name": api_name,
            "description": description[:3000],
            "runtime": "Deluge 1.0",
            "category": category,
        }
        if not code_as_file:
            fn["_code"] = code
        code_file = (f"{api_name}.ds", code) if code_as_file else None
        return self._audited(
            "create_function", api_name, "POST", "/settings/functions", {"functions": [fn]}, None,
            code_file,
        )  # fmt: skip

    # ------------------------------------------------------------------ Phase 4 (JSON APIs)

    def _json(
        self,
        action: str,
        target: str,
        method: str,
        path: str,
        body: dict | None = None,
        params: dict | None = None,
        before: Any = None,
    ) -> ZohoWriteResult:
        resp = self._http.request(
            method,
            f"{self.base}{path}",
            params=params,
            json=body,
            headers={"Authorization": f"Zoho-oauthtoken {self._tokens.get()}"},
        )
        try:
            payload = resp.json() if resp.content else {}
        except ValueError:
            payload = {"code": "HTTP_ERROR", "message": resp.text[:300]}
        results = (
            _all_results(payload)
            if payload
            else [{"status": "success" if resp.status_code < 300 else "error"}]
        )
        ok = resp.status_code < 300 and all(r.get("status") == "success" for r in results)
        first = results[0]
        self._audit.record(
            org=self.org.slug,
            environment=self.org.environment,
            action=action,
            target=target,
            result="success" if ok else f"error:{first.get('code', resp.status_code)}",
            before=before,
            after={
                "request": {"method": method, "path": path, "params": params, "body": body},
                "http_status": resp.status_code,
                "response": results,
            },  # fmt: skip
            project=self._project,
            plan_version=self._plan_version,
        )
        return ZohoWriteResult(
            status="success" if ok else "error",
            http_status=resp.status_code,
            code=first.get("code"),
            message=first.get("message"),
            details=first.get("details"),
            results=results,
        )

    def create_module(self, module: dict) -> ZohoWriteResult:
        return self._json(
            "create_module",
            module.get("plural_label", "?"),
            "POST",
            "/settings/modules",
            {"modules": [module]},
        )

    def create_fields(self, module: str, fields: list[dict]) -> ZohoWriteResult:
        if not 1 <= len(fields) <= 5:
            raise ValueError("Zoho accepts 1-5 fields per create call")
        names = ",".join(f["field_label"] for f in fields)
        return self._json(
            "create_fields",
            f"{module}:{names}",
            "POST",
            "/settings/fields",
            {"fields": fields},
            {"module": module},
        )

    def update_field(
        self, module: str, field_id: str, changes: dict, before: Any = None
    ) -> ZohoWriteResult:
        return self._json(
            "update_field",
            f"{module}:{field_id}",
            "PATCH",
            f"/settings/fields/{field_id}",
            {"fields": [changes]},
            {"module": module},
            before,
        )

    def delete_field(self, module: str, field_id: str, before: Any = None) -> ZohoWriteResult:
        return self._json(
            "delete_field",
            f"{module}:{field_id}",
            "DELETE",
            f"/settings/fields/{field_id}",
            None,
            {"module": module},
            before,
        )

    def patch_layout(
        self, module: str, layout_id: str, layout: dict, before: Any = None
    ) -> ZohoWriteResult:
        return self._json(
            "update_layout",
            f"{module}:{layout_id}",
            "PATCH",
            f"/settings/layouts/{layout_id}",
            {"layouts": [layout]},
            {"module": module},
            before,
        )

    def create_field_update(self, spec: dict) -> ZohoWriteResult:
        return self._json(
            "create_field_update",
            spec.get("name", "?"),
            "POST",
            "/settings/automation/field_updates",
            {"field_updates": [spec]},
        )

    def create_task(self, spec: dict) -> ZohoWriteResult:
        return self._json(
            "create_task_action",
            spec.get("name", "?"),
            "POST",
            "/settings/automation/tasks",
            {"tasks": [spec]},
        )

    def delete_field_update(self, action_id: str) -> ZohoWriteResult:
        return self._json(
            "delete_field_update",
            action_id,
            "DELETE",
            f"/settings/automation/field_updates/{action_id}",
        )

    def delete_task(self, action_id: str) -> ZohoWriteResult:
        return self._json(
            "delete_task_action", action_id, "DELETE", f"/settings/automation/tasks/{action_id}"
        )

    def create_workflow(self, rule: dict) -> ZohoWriteResult:
        return self._json(
            "create_workflow",
            rule.get("name", "?"),
            "POST",
            "/settings/automation/workflow_rules",
            {"workflow_rules": [rule]},
        )

    def set_workflow_active(
        self, rule_id: str, active: bool, before: Any = None
    ) -> ZohoWriteResult:
        body = {"workflow_rules": [{"id": rule_id, "status": {"active": active}}]}
        return self._json(
            "activate_workflow" if active else "deactivate_workflow",
            rule_id,
            "PUT",
            f"/settings/automation/workflow_rules/{rule_id}",
            body,
            None,
            before,
        )

    def delete_workflow(self, rule_id: str, before: Any = None) -> ZohoWriteResult:
        return self._json(
            "delete_workflow",
            rule_id,
            "DELETE",
            f"/settings/automation/workflow_rules/{rule_id}",
            None,
            None,
            before,
        )

    # ------------------------------------------------------------------ Phase 3 (functions)

    def update_function(
        self, function_id: str, api_name: str, code: str, changelog: str, before: dict
    ) -> ZohoWriteResult:
        metadata = {"functions": [{"_code": code}], "publish": {"changelog": changelog[:500]}}
        return self._audited(
            "update_function",
            api_name,
            "PUT",
            f"/settings/functions/{function_id}",
            metadata,
            before,
        )
