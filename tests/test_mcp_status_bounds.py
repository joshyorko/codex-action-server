"""Bounded, read-only MCP status inventory responses."""

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from test_actions import load_actions


class StatusClient:
    def __init__(self, native_result):
        self.native_result = native_result
        self.calls = []
        self.receipts = [{"accessToken": "receipt-secret"}]
        self.events = [{"authorization": "event-secret"}]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def provenance(self):
        return {"target": "fixture", "codexBin": "codex"}

    def request(self, method, params):
        self.calls.append((method, params))
        assert method == "mcpServerStatus/list"
        return self.native_result


def _depth(value):
    if isinstance(value, dict):
        return 1 + max((_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_depth(item) for item in value), default=0)
    return 0


def _deep_schema(depth):
    schema = {"type": "string"}
    for _ in range(depth):
        schema = {"type": "object", "properties": {"nested": schema}}
    return schema


@pytest.mark.parametrize(
    ("limit", "detail"),
    [(1, None), (1, "toolsAndAuthOnly"), (8, "toolsAndAuthOnly"), (8, "full")],
)
def test_mcp_status_preserves_native_page_and_detail_with_bounded_schema(limit, detail):
    tools = {
        f"tool-{index}": {
            "name": f"tool-{index}",
            "description": "Synthetic fixture only",
            "inputSchema": _deep_schema(80),
        }
        for index in range(400)
    }
    native = {
        "data": [
            {
                "name": f"server-{index}",
                "authStatus": "oAuth",
                "authorization": "native-secret",
                "serverInfo": {"name": "synthetic-server", "version": "1"},
                "tools": tools,
                "resources": [],
                "resourceTemplates": [],
            }
            for index in range(limit)
        ],
        "nextCursor": "opaque-native-cursor",
    }
    client = StatusClient(native)
    module = load_actions()
    request_values = {"target": "local", "limit": limit}
    if detail is not None:
        request_values["detail"] = detail

    with (
        patch.object(module, "Client", return_value=client),
        patch.object(module, "resolve_target", return_value="fixture"),
    ):
        response = module.list_mcp_server_status(
            module.McpServerStatusListRequest(**request_values)
        )

    result = response.result.result
    assert client.calls == [
        (
            "mcpServerStatus/list",
            {key: value for key, value in request_values.items() if key != "target"},
        )
    ]
    assert result["nextCursor"] == "opaque-native-cursor"
    assert len(result["data"]) == limit
    assert result["data"][0]["authStatus"] == "oAuth"
    assert result["data"][0]["name"] == "server-0"
    assert result["data"][0]["serverInfo"]["version"] == "1"
    assert result["data"][0]["tools"]["tool-0"]["name"] == "tool-0"
    assert result.get("truncated") is True
    assert result["_codexActionTruncation"]
    assert _depth(response.model_dump(mode="json")) <= 24
    assert (
        len(json.dumps(response.model_dump(mode="json"), ensure_ascii=False).encode())
        <= 262_144
    )
    assert response.result.receipts == []
    assert response.result.events == []
    assert "receipt-secret" not in repr(response.model_dump(mode="json"))
    assert "event-secret" not in repr(response.model_dump(mode="json"))
    assert "native-secret" not in repr(response.model_dump(mode="json"))


def test_mcp_status_preserves_opaque_cursor_without_clipping():
    module = load_actions()
    cursor = "opaque-" + "🔑" * 3000
    client = StatusClient({"data": [], "nextCursor": cursor})
    with (
        patch.object(module, "Client", return_value=client),
        patch.object(module, "resolve_target", return_value="fixture"),
    ):
        response = module.list_mcp_server_status(
            module.McpServerStatusListRequest(target="local", limit=1)
        )
    assert response.result.result["nextCursor"] == cursor


@pytest.mark.parametrize(
    "key", ["access_token", "authorization", "apiKey", "client_secret"]
)
def test_mcp_status_redacts_credential_shaped_root_metadata(key):
    module = load_actions()
    cursor = "opaque-native-cursor"
    client = StatusClient(
        {"data": [], "nextCursor": cursor, key: "root-credential-sentinel"}
    )
    with (
        patch.object(module, "Client", return_value=client),
        patch.object(module, "resolve_target", return_value="fixture"),
    ):
        response = module.list_mcp_server_status(
            module.McpServerStatusListRequest(target="local", limit=1)
        )
    assert response.result.result[key] == "[REDACTED]"
    assert response.result.result["nextCursor"] == cursor
    assert "root-credential-sentinel" not in repr(response.model_dump(mode="json"))


@pytest.mark.parametrize("cursor", [42, {}, "🔑" * 70_000])
def test_mcp_status_refuses_invalid_or_oversized_cursor(cursor):
    module = load_actions()
    client = StatusClient({"data": [], "nextCursor": cursor})
    with (
        patch.object(module, "Client", return_value=client),
        patch.object(module, "resolve_target", return_value="fixture"),
        pytest.raises(module.ActionError, match="Native MCP status") as error,
    ):
        module.list_mcp_server_status(
            module.McpServerStatusListRequest(target="local", limit=1)
        )
    assert "🔑" not in str(error.value)


@pytest.mark.parametrize("native", [{}, {"data": None}, {"data": [42]}])
def test_malformed_mcp_status_does_not_poison_following_read(native):
    module = load_actions()
    clients = [
        StatusClient(native),
        StatusClient({"data": [], "nextCursor": None}),
    ]
    with (
        patch.object(module, "Client", side_effect=clients),
        patch.object(module, "resolve_target", return_value="fixture"),
    ):
        request = module.McpServerStatusListRequest(target="local", limit=1)
        with pytest.raises(module.ActionError, match="Native MCP status"):
            module.list_mcp_server_status(request)
        assert module.list_mcp_server_status(request).result.result == {
            "data": [],
            "nextCursor": None,
        }


def test_mcp_status_concurrent_reads_do_not_mutate_native_inventory():
    module = load_actions()
    native = {
        "data": [{"name": "fixture", "tools": {"t": _deep_schema(80)}}],
        "nextCursor": "opaque",
    }
    original = json.dumps(native)
    with (
        patch.object(module, "Client", side_effect=lambda _: StatusClient(native)),
        patch.object(module, "resolve_target", return_value="fixture"),
        ThreadPoolExecutor(max_workers=4) as pool,
    ):
        request = module.McpServerStatusListRequest(target="local", limit=1)
        responses = list(
            pool.map(lambda _: module.list_mcp_server_status(request), range(8))
        )
    assert json.dumps(native) == original
    assert all(
        response.result.result == responses[0].result.result for response in responses
    )
