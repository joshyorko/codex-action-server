"""Shared integrations behavior; importing this module registers no actions."""

from __future__ import annotations

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
        lambda client: client.request("mcpServerStatus/list", params),
    )
