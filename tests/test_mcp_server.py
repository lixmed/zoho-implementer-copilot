import asyncio

from copilot.mcp_server.server import server


def _tools():
    return asyncio.run(server.list_tools())


def test_all_tools_are_read_only():
    tools = _tools()
    assert len(tools) >= 12
    for t in tools:
        assert t.annotations is not None and t.annotations.read_only_hint, t.name
        assert not t.annotations.destructive_hint, t.name


def test_no_write_or_delete_tools_exist():
    names = {t.name for t in _tools()}
    assert not any(n.startswith(("create", "update", "delete", "apply")) for n in names)
    assert {"list_orgs", "get_modules", "get_fields", "get_users"} <= names
