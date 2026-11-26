"""The ONLY code that writes to Zoho. Kept separate from the read client on purpose.

- Refuses production orgs outright (production safeguards arrive in Phase 7).
- Every call, success or failure, is written to the audit log with before/after.
- Phase 3 supports exactly two operations: create and update a Deluge function.
  Attaching a function to a workflow is NOT implemented (no verified API, needs approval).
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from copilot.audit.log import AuditLog
from copilot.orgs.registry import OrgProfile
from copilot.zoho.client import API_VERSION, TokenSource

FUNCTION_WRITE_SCOPES = ("ZohoCRM.settings.functions.CREATE", "ZohoCRM.settings.functions.UPDATE")


class WriteRefusedError(PermissionError):
    pass


class ZohoWriteResult(dict):
    @property
    def ok(self) -> bool:
        return self.get("status") == "success"


def _first_result(body: Any) -> dict:
    """Zoho wraps results as {"functions": [ {...} ]} or {"data": [...]}, or returns a flat error."""
    if isinstance(body, dict):
        for key in ("functions", "data"):
            if isinstance(body.get(key), list) and body[key]:
                return body[key][0]
        return body
    return {}


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
