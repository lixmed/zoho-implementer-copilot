"""Test adapter for the stateful fake Zoho that ships with the demo (copilot.demo.fakezoho)."""

from __future__ import annotations

import respx

from copilot.demo.fakezoho import API
from copilot.demo.fakezoho import FakeOrg as _FakeOrg

__all__ = ["API", "FakeOrg"]


class FakeOrg(_FakeOrg):
    def mount(self, router: respx.Router) -> None:
        router.route(url__startswith=API).mock(side_effect=self.handle)
