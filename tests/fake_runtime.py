"""Fake Zoho runtime for the Deluge test loop: records (create/read/delete) and function
execution, where each "function" is a Python stand-in for its Deluge logic."""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from urllib.parse import parse_qs

import httpx
import respx

API = "https://developer.zohoapis.com/crm/v8"


class FakeRuntime:
    def __init__(self):
        self.ids = itertools.count(5000)
        self.records: dict[str, dict[str, dict]] = {}
        self.functions: dict[str, Callable[[dict, FakeRuntime], dict]] = {}
        self.rest_enabled: set[str] = set()
        self.calls: list[dict] = []

    def define(self, name: str, fn: Callable[[dict, FakeRuntime], dict], rest: bool = True) -> None:
        self.functions[name] = fn
        if rest:
            self.rest_enabled.add(name)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/crm/v8", 1)[1]
        m = request.method
        if m == "GET" and path == "/settings/functions":
            return httpx.Response(
                200,
                json={
                    "functions": [
                        {"api_name": n, "id": f"fn-{n}", "rest_api_mode": ["Oauth"]}
                        for n in self.functions
                    ]
                },
            )
        if path.startswith("/functions/") and path.endswith("/actions/execute") and m == "POST":
            name = path.split("/")[2]
            form = parse_qs(request.content.decode())
            args = json.loads(form.get("arguments", ["{}"])[0])
            auth = "apikey" if request.url.params.get("auth_type") == "apikey" else "oauth"
            self.calls.append(
                {
                    "name": name,
                    "args": args,
                    "auth": auth,
                    "has_header": "authorization" in request.headers,
                }
            )
            if name not in self.rest_enabled:
                return httpx.Response(
                    401, json={"code": "OAUTH_SCOPE_MISMATCH", "message": "invalid oauth scope"}
                )
            result = self.functions[name](args, self)
            return httpx.Response(200, json={"code": "success", "message": "function executed successfully",
                                             "details": {"output": json.dumps(result), "output_type": "string"}})  # fmt: skip
        parts = path.strip("/").split("/")
        if len(parts) == 1 and m == "POST":
            body = json.loads(request.content)
            assert body.get("trigger") == [], "test records must suppress automation"
            rid = str(next(self.ids))
            self.records.setdefault(parts[0], {})[rid] = {"id": rid, **body["data"][0]}
            return httpx.Response(
                201,
                json={
                    "data": [
                        {
                            "code": "SUCCESS",
                            "status": "success",
                            "message": "record added",
                            "details": {"id": rid},
                        }
                    ]
                },
            )
        if len(parts) == 2 and m == "GET":
            rec = self.records.get(parts[0], {}).get(parts[1])
            return httpx.Response(200, json={"data": [rec]}) if rec else httpx.Response(204)
        if len(parts) == 2 and m == "DELETE":
            self.records.get(parts[0], {}).pop(parts[1], None)
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "code": "SUCCESS",
                            "status": "success",
                            "message": "record deleted",
                            "details": {"id": parts[1]},
                        }
                    ]
                },
            )
        return httpx.Response(
            404, json={"code": "INVALID_URL_PATTERN", "message": f"fake runtime: {m} {path}"}
        )

    def mount(self, router: respx.Router) -> None:
        router.route(url__regex=rf"{API}/(settings/functions$|functions/|Leads|Deals)").mock(
            side_effect=self.handle
        )
