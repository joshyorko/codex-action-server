"""Persisted discovery requires an explicit worktree before native access."""

from unittest.mock import patch

from pydantic import ValidationError
import pytest

from test_actions import FakeClient, load_actions
from test_action_server_validation import _result_data


async def assert_mcp_cwd_rejected(session, name, payload):
    from mcp.shared.exceptions import MCPError

    try:
        result = await session.call_tool(name, {"payload": payload})
    except MCPError as error:
        # Runtime schema validation rejects the call at the JSON-RPC boundary.
        assert "cwd" in str(error).lower()
    else:
        # ActionError uses the Actions Runtime's typed error envelope, even when
        # the MCP result itself is not marked is_error.
        error = result.structured_content
        assert isinstance(error, dict), payload
        assert error.get("result") is None, error
        assert isinstance(error.get("error"), str), error
        assert "cwd" in error["error"].lower(), error


async def assert_scoped_mcp_discovery(session, tools, native):
    """Exercise the published schema and calls through the real Action Server."""
    schema = tools["discover_threads"].input_schema["properties"]["payload"]
    assert "cwd" in schema["required"]
    assert schema["properties"]["cwd"]["type"] == "string"
    assert "default" not in schema["properties"]["cwd"]
    assert "exact" in schema["properties"]["cwd"]["description"].lower()
    assert "worktree" in tools["discover_threads"].description.lower()

    before = list(native.calls)
    for scope in (
        {},
        {"cwd": None},
        {"cwd": ""},
        {"cwd": "relative/path"},
        {"cwd": "/work/../other"},
    ):
        await assert_mcp_cwd_rejected(
            session, "discover_threads", {"target": "local", **scope}
        )
    assert native.calls == before, "Unscoped discovery reached the native daemon"

    expected = native.discovery_records
    page = _result_data(
        await session.call_tool(
            "discover_threads",
            {"payload": {"target": "local", "cwd": native.cwd, "limit": 1}},
        )
    )["result"]
    assert page["data"] == expected[:1]  # Rich persisted metadata is preserved.
    assert page["nextCursor"] == "fixture-offset-1"
    next_page = _result_data(
        await session.call_tool(
            "discover_threads",
            {
                "payload": {
                    "target": "local",
                    "cwd": native.cwd,
                    "limit": 1,
                    "cursor": page["nextCursor"],
                }
            },
        )
    )["result"]
    assert next_page == {"data": expected[1:2], "nextCursor": None}

    other_cwd = expected[-1]["cwd"]
    other = _result_data(
        await session.call_tool(
            "discover_threads", {"payload": {"target": "local", "cwd": other_cwd}}
        )
    )["result"]
    assert other == {"data": expected[-1:], "nextCursor": None}

    for record in (expected[0], expected[-1]):
        read = _result_data(
            await session.call_tool(
                "read_thread",
                {
                    "payload": {
                        "target": "local",
                        "cwd": record["cwd"],
                        "thread_id": record["id"],
                    }
                },
            )
        )["result"]["thread"]
        assert read["id"] == record["id"]
        assert read["cwd"] == record["cwd"]

    await assert_mcp_cwd_rejected(
        session,
        "read_thread",
        {"target": "local", "cwd": other_cwd, "thread_id": native.thread_id},
    )
    calls = native.calls[len(before) :]
    assert {call["method"] for call in calls} <= {
        "initialize",
        "initialized",
        "thread/list",
        "thread/read",
    }
    assert [call["params"] for call in calls if call["method"] == "thread/list"] == [
        {"cwd": native.cwd, "limit": 1},
        {"cwd": native.cwd, "limit": 1, "cursor": "fixture-offset-1"},
        {"cwd": other_cwd, "limit": 50},
    ]


@pytest.mark.parametrize("arguments", [{}, {"cwd": None}])
def test_discovery_requires_a_nonnullable_cwd(arguments):
    actions = load_actions()
    with pytest.raises(ValidationError):
        actions.ThreadListRequest(target="local", **arguments)


def test_discovery_schema_makes_the_scope_required():
    schema = load_actions().ThreadListRequest.model_json_schema()
    assert "cwd" in schema["required"]
    assert schema["properties"]["cwd"]["type"] == "string"
    assert "default" not in schema["properties"]["cwd"]


@pytest.mark.parametrize(
    "cwd", ["", " ", "relative/path", "/work/../other", "/work\nother"]
)
def test_discovery_rejects_invalid_cwd_before_connecting(cwd):
    actions = load_actions()
    with patch.object(actions, "Client") as client:
        with pytest.raises(actions.ActionError, match="cwd"):
            actions.discover_threads(actions.ThreadListRequest(target="local", cwd=cwd))
    client.assert_not_called()


def test_discovery_keeps_exact_scope_on_every_page():
    actions = load_actions()
    client = FakeClient(None)
    with patch.object(actions, "Client", return_value=client):
        actions.discover_threads(
            actions.ThreadListRequest(
                target="local", cwd="/trusted", limit=1, cursor="page-2"
            )
        )
    assert client.calls == [
        ("thread/list", {"cwd": "/trusted", "limit": 1, "cursor": "page-2"})
    ]
