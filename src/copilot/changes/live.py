"""Live org state for the modules a change set touches (raw ids included, lazily loaded).

Used to (1) diff the plan against reality before applying, (2) resolve names to ids
(layouts, sections, fields, profiles), and (3) snapshot before/after for rollback and audit.
Metadata only: no records are read.
"""

from __future__ import annotations

from copilot.zoho.client import ZohoReadClient


class LiveOrg:
    def __init__(self, client: ZohoReadClient):
        self.c = client
        self._modules: dict[str, dict] | None = None
        self._fields: dict[str, dict[str, dict]] = {}
        self._layouts: dict[str, list[dict]] = {}
        self._workflows: list[dict] | None = None
        self._profiles: list[dict] | None = None

    # ---- loaders (fresh=True bypasses the read client's cache, for after-snapshots)

    def modules(self, fresh: bool = False) -> dict[str, dict]:
        if self._modules is None or fresh:
            mods = self.c.get("/settings/modules", use_cache=not fresh).get("modules", [])
            self._modules = {m["api_name"]: m for m in mods}
        return self._modules

    def fields(self, module: str, fresh: bool = False) -> dict[str, dict]:
        if module not in self._fields or fresh:
            if module not in self.modules():
                self._fields[module] = {}
            else:
                body = self.c.get("/settings/fields", {"module": module}, use_cache=not fresh)
                self._fields[module] = {f["api_name"]: f for f in body.get("fields", [])}
        return self._fields[module]

    def layouts(self, module: str, fresh: bool = False) -> list[dict]:
        if module not in self._layouts or fresh:
            if module not in self.modules():
                self._layouts[module] = []
            else:
                body = self.c.get("/settings/layouts", {"module": module}, use_cache=not fresh)
                self._layouts[module] = body.get("layouts", [])
        return self._layouts[module]

    def workflows(self, fresh: bool = False) -> list[dict]:
        if self._workflows is None or fresh:
            body = self.c.get(
                "/settings/automation/workflow_rules", {"per_page": 200}, use_cache=not fresh
            )
            self._workflows = body.get("workflow_rules", [])
        return self._workflows

    def profiles(self) -> list[dict]:
        if self._profiles is None:
            self._profiles = self.c.get("/settings/profiles").get("profiles", [])
        return self._profiles

    def picklist_values(self, module: str, field_id: str) -> list[dict]:
        body = self.c.get(
            f"/settings/fields/{field_id}/pick_list_values", {"module": module}, use_cache=False
        )
        return body.get("pick_list_values", [])

    # ---- lookups

    def field(self, module: str, api_name: str) -> dict | None:
        return self.fields(module).get(api_name)

    def layout_by_name(self, module: str, name: str) -> dict | None:
        return next((lay for lay in self.layouts(module) if lay.get("name") == name), None)

    @staticmethod
    def section_by_label(layout: dict, label: str) -> dict | None:
        for s in layout.get("sections", []):
            if label in (s.get("display_label"), s.get("name")):
                return s
        return None

    @staticmethod
    def find_field_in_layout(layout: dict, field_id: str) -> tuple[dict, dict] | None:
        for s in layout.get("sections", []):
            for f in s.get("fields", []):
                if str(f.get("id")) == str(field_id):
                    return s, f
        return None

    def layouts_containing(self, module: str, field_id: str, fresh: bool = False) -> list[dict]:
        return [
            lay for lay in self.layouts(module, fresh) if self.find_field_in_layout(lay, field_id)
        ]

    def workflow_named(self, name: str) -> dict | None:
        return next((w for w in self.workflows() if w.get("name") == name), None)

    def profile_ids(self, names: list[str]) -> tuple[list[str], list[str]]:
        by_name = {p.get("name"): str(p.get("id")) for p in self.profiles()}
        found = [by_name[n] for n in names if n in by_name]
        missing = [n for n in names if n not in by_name]
        return found, missing

    def snapshot(self, modules: list[str]) -> dict:
        """Metadata snapshot of the touched modules (fields, layouts) plus workflow rules."""
        return {
            "modules": sorted(m for m in modules if m in self.modules()),
            "fields": {m: self.fields(m) for m in modules if m in self.modules()},
            "layouts": {m: self.layouts(m) for m in modules if m in self.modules()},
            "workflow_rules": self.workflows(),
        }

    def refresh(self, modules: list[str]) -> None:
        self.modules(fresh=True)
        for m in modules:
            self.fields(m, fresh=True)
            self.layouts(m, fresh=True)
        self.workflows(fresh=True)
