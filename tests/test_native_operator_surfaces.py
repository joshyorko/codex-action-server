"""Typed review, account, inventory, MCP, and terminal action contracts."""

from unittest.mock import patch

import pytest

from test_actions import FakeClient, load_actions
from native_wire_contracts import (
    assert_native_request_contract,
    assert_native_response_contract,
)


class NativeSurfaceClient(FakeClient):
    def request(self, method, params):
        self.calls.append((method, params))
        if method == "thread/read":
            return {"thread": {"id": params["threadId"], "cwd": "/trusted"}}
        if method == "account/rateLimits/read":
            return {"rateLimits": {}}
        if method == "account/usage/read":
            return {"summary": {"lifetimeTokens": 1}}
        if method in {"skills/list", "hooks/list", "plugin/list"}:
            return {"data": []}
        if method == "plugin/read":
            return {"plugin": {"name": params["pluginName"]}}
        if method == "app/list":
            return {"data": [], "nextCursor": None}
        if method == "app/read":
            app_id = params["appIds"][0]
            return {
                "apps": [{"id": app_id, "name": "Docs"}],
                "missingAppIds": [],
            }
        if method == "mcpServer/resource/read":
            return {"contents": [{"uri": params["uri"], "text": "resource"}]}
        if method == "mcpServer/tool/call":
            return {"content": [{"type": "text", "text": "tool result"}]}
        if method == "review/start":
            return {
                "reviewThreadId": params["threadId"],
                "turn": {"id": "review-turn"},
            }
        if method == "thread/backgroundTerminals/list":
            return {
                "data": [
                    {
                        "itemId": "item-1",
                        "processId": "process-1",
                        "cwd": "/trusted",
                        "command": "safe command",
                    },
                    {
                        "itemId": "item-2",
                        "processId": "process-2",
                        "cwd": "/other",
                        "command": "filtered command",
                    },
                ],
                "nextCursor": None,
            }
        if method == "thread/backgroundTerminals/terminate":
            return {"terminated": True}
        raise AssertionError(method)


def test_reads_are_target_and_cwd_scoped_with_exact_native_params():
    module = load_actions()
    client = NativeSurfaceClient(None)
    with patch.object(module, "Client", return_value=client):
        module.read_account_rate_limits(module.AccountRateLimitsRequest(target="local"))
        module.read_account_usage(
            module.AccountUsageRequest(
                target="local", thread_id="thread-1", cwd="/trusted"
            )
        )
        module.list_skills(module.CwdInventoryRequest(target="local", cwd="/trusted"))
        module.list_hooks(module.CwdInventoryRequest(target="local", cwd="/trusted"))
        module.list_plugins(module.CwdInventoryRequest(target="local", cwd="/trusted"))
        module.read_plugin(module.PluginReadRequest(target="local", plugin_name="docs"))
        module.list_apps(
            module.AppsListRequest(
                target="local",
                limit=10,
                thread_id="thread-1",
                cwd="/trusted",
            )
        )
        resource = module.read_mcp_resource(
            module.McpResourceReadRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                server="docs",
                uri="file://guide",
            )
        )

    assert resource.result.receipts == []
    assert [method for method, _params in client.calls] == [
        "account/rateLimits/read",
        "thread/read",
        "account/usage/read",
        "skills/list",
        "hooks/list",
        "plugin/list",
        "plugin/read",
        "thread/read",
        "app/list",
        "thread/read",
        "mcpServer/resource/read",
    ]
    assert client.calls[0][1] == {}
    assert client.calls[2][1] == {"threadId": "thread-1"}
    assert client.calls[3][1] == {"cwds": ["/trusted"]}
    assert client.calls[4][1] == {"cwds": ["/trusted"]}
    assert client.calls[5][1] == {"cwds": ["/trusted"]}
    assert client.calls[6][1] == {"pluginName": "docs"}
    assert client.calls[8][1] == {"limit": 10, "threadId": "thread-1"}
    assert client.calls[10][1] == {
        "threadId": "thread-1",
        "server": "docs",
        "uri": "file://guide",
    }


def test_app_read_uses_pinned_fields_and_checks_exact_response_identity():
    module = load_actions()
    client = NativeSurfaceClient(None)
    with patch.object(module, "Client", return_value=client):
        response = module.read_app(
            module.AppReadRequest(
                target="local",
                app_id="docs",
                include_tools=True,
                thread_id="thread-1",
                cwd="/trusted",
            )
        )

    assert response.result.result["apps"] == [{"id": "docs", "name": "Docs"}]
    assert client.calls[-1] == (
        "app/read",
        {"appIds": ["docs"], "includeTools": True, "threadId": "thread-1"},
    )
    assert_native_request_contract("app/read", client.calls[-1][1])
    assert_native_response_contract("app/read", response.result.result)
    with pytest.raises(module.RpcError, match="identity mismatch"):
        module._validate_app_read_response(
            {"apps": [{"id": "other", "name": "Wrong"}], "missingAppIds": []},
            "docs",
        )
    with pytest.raises(ValueError, match="supplied together"):
        module.AppReadRequest(target="local", app_id="docs", thread_id="thread-1")


def test_review_and_mcp_tool_calls_are_consequential_and_receipt_protected(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_ACTION_RECEIPTS", str(tmp_path))
    module = load_actions()
    client = NativeSurfaceClient(None)

    with patch.object(module, "Client", return_value=client):
        review = module.start_review(
            module.ReviewStartRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                request_id="review-once",
                review_target={"type": "baseBranch", "branch": "main"},
            )
        ).result.result
        tool = module.call_mcp_tool(
            module.McpToolCallRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                request_id="mcp-once",
                server="docs",
                tool="search",
                arguments={"query": "guide"},
            )
        ).result.result

    assert review["dispatch"]["turn_id"] == "review-turn"
    assert tool["dispatch"]["state"] == "accepted"
    assert client.calls[1] == (
        "review/start",
        {
            "threadId": "thread-1",
            "target": {"type": "baseBranch", "branch": "main"},
        },
    )
    assert client.calls[3] == (
        "mcpServer/tool/call",
        {
            "threadId": "thread-1",
            "server": "docs",
            "tool": "search",
            "arguments": {"query": "guide"},
        },
    )


def test_terminal_list_filters_worktree_and_termination_reconciles_exact_process(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_ACTION_RECEIPTS", str(tmp_path))
    module = load_actions()
    client = NativeSurfaceClient(None)

    with patch.object(module, "Client", return_value=client):
        listed = module.list_background_terminals(
            module.ThreadBackgroundTerminalsListRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                limit=10,
            )
        ).result
        terminated = module.terminate_background_terminal(
            module.ThreadBackgroundTerminalTerminateRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                request_id="terminate-one",
                process_id="process-1",
            )
        ).result.result

    assert [item["processId"] for item in listed.result["data"]] == ["process-1"]
    assert listed.receipts == []
    assert terminated["terminated"] is True
    assert terminated["dispatch"]["state"] == "accepted"
    assert client.calls[-1] == (
        "thread/backgroundTerminals/terminate",
        {"threadId": "thread-1", "processId": "process-1"},
    )
    assert_native_request_contract(
        "thread/backgroundTerminals/terminate", client.calls[-1][1]
    )
    assert_native_request_contract(
        "thread/backgroundTerminals/list",
        {
            "threadId": "thread-1",
            "limit": 10,
        },
    )
    assert_native_response_contract("thread/backgroundTerminals/list", listed.result)
    assert_native_response_contract(
        "thread/backgroundTerminals/terminate",
        {"terminated": terminated["terminated"]},
    )


def test_mcp_tool_arguments_and_account_thread_scope_are_strict():
    module = load_actions()
    with pytest.raises(ValueError, match="16 KiB"):
        module.McpToolCallRequest(
            target="local",
            cwd="/trusted",
            thread_id="thread-1",
            request_id="large",
            server="docs",
            tool="search",
            arguments={"payload": "x" * 16_385},
        )
    with pytest.raises(ValueError, match="supplied together"):
        module.AccountUsageRequest(
            target="local",
            thread_id="thread-1",
        )
