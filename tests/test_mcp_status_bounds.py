"""Bounded, read-only MCP status inventory responses."""

import json
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
