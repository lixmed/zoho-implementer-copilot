import asyncio

from copilot.mcp_server.server import server

# Tools that may write, and only to the LOCAL workspace (never to Zoho)
LOCAL_WRITE_TOOLS = {"save_plan", "save_deluge"}


def _tools():
    return asyncio.run(server.list_tools())


def test_tools_are_read_only_except_local_plan_saving():
    tools = _tools()
    assert len(tools) >= 19
    for t in tools:
        assert t.annotations is not None, t.name
        assert not t.annotations.destructive_hint, t.name
        if t.name in LOCAL_WRITE_TOOLS:
            assert not t.annotations.open_world_hint, f"{t.name} must not reach external systems"
        else:
            assert t.annotations.read_only_hint, t.name


def test_no_zoho_write_delete_or_approve_tools():
    names = {t.name for t in _tools()}
    forbidden = ("create", "update", "delete", "apply", "approve", "push", "attach", "deploy")
    assert not any(n.startswith(forbidden) for n in names)
    assert {"list_orgs", "get_fields", "get_requirements", "check_plan", "save_plan"} <= names
