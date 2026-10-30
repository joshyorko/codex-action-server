"""Shared integrations behavior; importing this module registers no actions."""

from __future__ import annotations

import json
import math
from typing import Any
from codex_shared.models import AppReadRequest
from codex_shared.models import AppsListRequest
from codex_shared.models import CwdInventoryRequest
from codex_shared.models import McpResourceReadRequest
from codex_shared.models import McpServerStatusListRequest
from codex_shared.models import McpToolCallRequest
from codex_shared.models import PluginReadRequest
from actions import Response
from codex_shared.models import RpcEnvelope
from codex_rpc import RpcError
from codex_shared.common import _action_cwd
from codex_shared.common import _action_id
from codex_shared.common import _bounded_native_result
from codex_shared.common import _dispatch_run
from codex_shared.common import _read_guarded_thread
from codex_shared.common import _run


_MCP_STATUS_MAX_DEPTH = 16
_MCP_STATUS_MAX_BYTES = 262_144
_MCP_STATUS_TRUNCATION_KEY = "_codexActionTruncation"
_MCP_STATUS_PRIORITY = {
    "nextCursor": 0,
    "name": 1,
    "status": 2,
    "authStatus": 3,
    "serverInfo": 4,
    "tools": 5,
    "resources": 6,
    "resourceTemplates": 7,
    "title": 8,
    "description": 9,
    "type": 10,
    "inputSchema": 11,
    "outputSchema": 12,
}
_MCP_STATUS_SECRET_KEYS = {
    "accesstoken",
    "refreshtoken",
    "clientsecret",
    "apikey",
    "authorization",
    "password",
    "credential",
    "credentials",
}


def _bounded_mcp_status_result(result: Any) -> dict[str, Any]:
    """Keep MCP status pages useful while bounding nested native inventories."""
    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        raise RpcError("Native MCP status response is missing its data page")
    servers = result["data"]
    if len(servers) > 100:
        raise RpcError("Native MCP status page exceeds the 100-server limit")
    if any(not isinstance(server, dict) for server in servers):
        raise RpcError("Native MCP status page contains an invalid server entry")

    for per_server_budget in (1024, 512, 256, 128, 64):
        for max_string_chars in (2048, 1024, 512, 256):
            truncations: list[dict[str, Any]] = []

            def record(path: str, reason: str, omitted: int | None = None) -> None:
                if len(truncations) < 32:
                    item: dict[str, Any] = {
                        "path": path[:max_string_chars],
                        "reason": reason,
                    }
                    if omitted is not None:
                        item["omittedItems"] = omitted
                    truncations.append(item)

            def visit(value: Any, path: str, depth: int, budget: list[int]) -> Any:
                if budget[0] <= 0:
                    record(path, "item_limit")
                    return None
                budget[0] -= 1

                if isinstance(value, str):
                    if len(value) > max_string_chars:
                        record(path, "string_limit")
                        return value[:max_string_chars] + "…"
                    return value
                if value is None or isinstance(value, (bool, int)):
                    return value
                if isinstance(value, float):
                    if math.isfinite(value):
                        return value
                    raise RpcError("Native MCP status response is not JSON data")
                if isinstance(value, list):
                    if depth >= _MCP_STATUS_MAX_DEPTH:
                        record(path, "depth_limit")
                        return []
                    bounded = []
                    for index, item in enumerate(value):
                        if budget[0] <= 0:
                            record(path, "item_limit", len(value) - index)
                            break
                        bounded.append(
                            visit(item, f"{path}[{index}]", depth + 1, budget)
                        )
                    return bounded
                if isinstance(value, dict):
                    if depth >= _MCP_STATUS_MAX_DEPTH:
                        record(path, "depth_limit")
                        return {"_truncated": True, "reason": "depth_limit"}
                    bounded = {}
                    keys = sorted(
                        value,
                        key=lambda key: (_MCP_STATUS_PRIORITY.get(key, 100), str(key)),
                    )
                    for index, key in enumerate(keys):
                        if budget[0] <= 0:
                            record(path, "item_limit", len(keys) - index)
                            break
                        if not isinstance(key, str):
                            raise RpcError(
                                "Native MCP status response is not JSON data"
                            )
                        child_path = f"{path}.{key}" if path else str(key)
                        if len(key) > max_string_chars:
                            record(child_path, "key_limit")
                            continue
                        normalized_key = str(key).replace("_", "").lower()
                        if normalized_key in _MCP_STATUS_SECRET_KEYS:
                            bounded[key] = "[REDACTED]"
                        else:
                            bounded[key] = visit(
                                value[key], child_path, depth + 1, budget
                            )
                    return bounded
                raise RpcError("Native MCP status response is not JSON data")

            bounded_result: dict[str, Any] = {}
            root_budget = [2048]
            ordered_keys = sorted(
                result,
                key=lambda key: (_MCP_STATUS_PRIORITY.get(key, 100), str(key)),
            )
            for key in ordered_keys:
                if key == "data":
                    bounded_result[key] = [
                        visit(
                            server,
                            f"data[{index}]",
                            2,
                            [per_server_budget],
                        )
                        for index, server in enumerate(servers)
                    ]
                else:
                    bounded_result[key] = visit(result[key], str(key), 1, root_budget)

            if truncations:
                bounded_result["truncated"] = True
                bounded_result[_MCP_STATUS_TRUNCATION_KEY] = truncations
            encoded = json.dumps(
                bounded_result,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) <= _MCP_STATUS_MAX_BYTES:
                return bounded_result

    raise RpcError("Native MCP status page exceeds the bounded response limit")


def _validate_app_read_response(result: Any, requested_app_id: str) -> None:
    if not isinstance(result, dict):
        raise RpcError("Native app/read response is not an object")
    apps = result.get("apps")
    missing = result.get("missingAppIds")
    if not isinstance(apps, list) or not isinstance(missing, list):
        raise RpcError("Native app/read response is missing its apps or missingAppIds")
    returned_ids = [app.get("id") if isinstance(app, dict) else None for app in apps]
    if (
        len(apps) > 1
        or returned_ids not in ([requested_app_id], [])
        or missing not in ([requested_app_id], [])
        or bool(apps) == bool(missing)
    ):
        raise RpcError("Native app/read response identity mismatch")
    if apps and not isinstance(apps[0].get("name"), str):
        raise RpcError("Native app/read metadata is missing its name")


def list_skills(payload: CwdInventoryRequest) -> Response[RpcEnvelope]:
    """List skills discovered for one exact worktree.

    Args:
        payload: Configured target and exact absolute worktree path.
    """
    cwd = _action_cwd(payload.cwd)
    return _run(
        "list_skills",
        payload.target,
        lambda client: client.request("skills/list", {"cwds": [cwd]}),
    )


def list_hooks(payload: CwdInventoryRequest) -> Response[RpcEnvelope]:
    """List hooks discovered for one exact worktree.

    Args:
        payload: Configured target and exact absolute worktree path.
    """
    cwd = _action_cwd(payload.cwd)
    return _run(
        "list_hooks",
        payload.target,
        lambda client: client.request("hooks/list", {"cwds": [cwd]}),
    )


def list_plugins(payload: CwdInventoryRequest) -> Response[RpcEnvelope]:
    """List native plugins associated with one exact worktree.

    Args:
        payload: Configured target and exact absolute worktree path.
    """
    cwd = _action_cwd(payload.cwd)
    return _run(
        "list_plugins",
        payload.target,
        lambda client: client.request("plugin/list", {"cwds": [cwd]}),
    )


def read_plugin(payload: PluginReadRequest) -> Response[RpcEnvelope]:
    """Read one named plugin from the native plugin catalog.

    Args:
        payload: Configured target and exact native plugin name.
    """
    return _run(
        "read_plugin",
        payload.target,
        lambda client: _bounded_native_result(
            client.request("plugin/read", {"pluginName": payload.plugin_name})
        ),
        include_receipts=False,
    )


def list_apps(payload: AppsListRequest) -> Response[RpcEnvelope]:
    """List a bounded page of native apps/connectors.

    Args:
        payload: Target and bounded pagination; optional exact thread/CWD scope.
    """
    params: dict[str, Any] = {"limit": payload.limit}
    if payload.cursor is not None:
        params["cursor"] = payload.cursor
    if payload.thread_id is not None:
        thread_id = _action_id(payload.thread_id, "thread_id")
        cwd = _action_cwd(payload.cwd)
        params["threadId"] = thread_id

        def invoke(client):
            _read_guarded_thread(client, thread_id, cwd)
            return client.request("app/list", params)

    else:

        def invoke(client):
            return client.request("app/list", params)

    return _run("list_apps", payload.target, invoke)


def read_app(payload: AppReadRequest) -> Response[RpcEnvelope]:
    """Read bounded native metadata for one exact app identifier.

    Args:
        payload: Target, exact app ID, optional tool summaries, and optional thread/CWD.
    """
    app_id = _action_id(payload.app_id, "app_id")
    params: dict[str, Any] = {"appIds": [app_id]}
    if payload.include_tools:
        params["includeTools"] = True
    if payload.thread_id is not None:
        thread_id = _action_id(payload.thread_id, "thread_id")
        cwd = _action_cwd(payload.cwd)
        params["threadId"] = thread_id

        def invoke(client):
            _read_guarded_thread(client, thread_id, cwd)
            result = client.request("app/read", params)
            _validate_app_read_response(result, app_id)
            return _bounded_native_result(result)

    else:

        def invoke(client):
            result = client.request("app/read", params)
            _validate_app_read_response(result, app_id)
            return _bounded_native_result(result)

    return _run("read_app", payload.target, invoke)


def read_mcp_resource(payload: McpResourceReadRequest) -> Response[RpcEnvelope]:
    """Read one named native MCP resource with exact thread/CWD scope.

    Args:
        payload: Target, thread identity, server name, and resource URI.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    params = {
        "threadId": thread_id,
        "server": payload.server,
        "uri": payload.uri,
    }

    def invoke(client):
        _read_guarded_thread(client, thread_id, cwd)
        return _bounded_native_result(client.request("mcpServer/resource/read", params))

    return _run("read_mcp_resource", payload.target, invoke, include_receipts=False)


def call_mcp_tool(payload: McpToolCallRequest) -> Response[RpcEnvelope]:
    """Call one explicitly named native MCP tool under Codex's native authority.

    Args:
        payload: Exact thread, server/tool names, bounded arguments, and receipt key.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    params: dict[str, Any] = {
        "threadId": thread_id,
        "server": payload.server,
        "tool": payload.tool,
    }
    if payload.arguments is not None:
        params["arguments"] = payload.arguments

    def invoke(client, receipt):
        _read_guarded_thread(client, thread_id, cwd)
        result = _bounded_native_result(client.request("mcpServer/tool/call", params))
        receipt.update(state="accepted", thread_id=thread_id)
        return result

    return _dispatch_run("call_mcp_tool", payload, invoke)


def list_mcp_server_status(
    payload: McpServerStatusListRequest,
) -> Response[RpcEnvelope]:
    """List native MCP status with optional bounded inventory detail.

    Args:
        payload: Target, optional thread, bounded page, and inventory detail.
    """
    params: dict[str, Any] = {}
    if payload.thread_id is not None:
        params["threadId"] = _action_id(payload.thread_id, "thread_id")
    if payload.limit is not None:
        params["limit"] = payload.limit
    if payload.cursor is not None:
        params["cursor"] = payload.cursor
    if payload.detail is not None:
        params["detail"] = payload.detail
    return _run(
        "mcpServerStatus/list",
        payload.target,
        lambda client: _bounded_mcp_status_result(
            client.request("mcpServerStatus/list", params)
        ),
        include_receipts=False,
        include_events=False,
        maximum_response_bytes=_MCP_STATUS_MAX_BYTES,
    )
