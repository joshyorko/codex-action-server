"""Shared inspection behavior; importing this module registers no actions."""

from __future__ import annotations

from codex_shared.models import AccountRateLimitsRequest
from codex_shared.models import AccountUsageRequest
from typing import Any
from codex_shared.models import DispatchReceiptRequest
from codex_shared.models import ModelListRequest
from codex_shared.models import ModelProviderCapabilitiesRequest
from codex_shared.models import NativeCapabilitiesRequest
from actions import Response
from codex_shared.models import RpcEnvelope
from codex_shared.models import ServerDiagnosticsRequest
from codex_shared.common import _action_cwd
from codex_shared.common import _action_id
from codex_shared.common import _read_guarded_thread
from codex_shared.common import _receipt_root
from codex_shared.common import _run
from boundary import configurations
import dispatch_receipts
from native_capabilities import inventory as native_capability_inventory
from codex_rpc import native_server_build_version
from boundary import resolve_target


def read_dispatch_receipt(payload: DispatchReceiptRequest) -> Response[dict[str, Any]]:
    """Read a dispatch acknowledgement; native Codex owns current turn status.

    Args:
        payload: The exact client-generated request ID.
    """
    return Response(result=dispatch_receipts.read(_receipt_root(), payload.request_id))


def list_targets() -> Response[dict[str, Any]]:
    """List operator-configured logical target names without exposing credentials."""
    return Response(
        result={
            "targets": [
                {"name": name, "transport": config.get("transport")}
                for name, config in configurations().items()
            ]
        }
    )


def inspect_target(payload: ServerDiagnosticsRequest) -> Response[dict[str, Any]]:
    """Resolve a logical target without starting services or changing native state.

    Args:
        payload: The configured logical target to resolve.
    """
    try:
        target = resolve_target(payload.target)
        return Response(
            result={
                "target": payload.target,
                "status": "resolved",
                "destination": target.target,
                "codex_bin": target.codex_bin,
                "socket": target.socket_path,
                "connectivity": "not_probed",
            }
        )
    except ValueError as error:
        return Response(
            result={
                "target": payload.target,
                "status": "unresolved",
                "error_code": str(error),
            }
        )


def list_native_capabilities(
    payload: NativeCapabilitiesRequest,
) -> Response[RpcEnvelope]:
    """List the pinned native inventory and this server's exposure profile.

    Args:
        payload: The configured target whose native version should be reported.
    """
    return _run(
        "list_native_capabilities",
        payload.target,
        lambda client: native_capability_inventory(
            native_server_build_version(client.metadata.get("userAgent"))
        ),
    )


def read_account_rate_limits(
    payload: AccountRateLimitsRequest,
) -> Response[RpcEnvelope]:
    """Read native account rate-limit status without consuming credits.

    Args:
        payload: The configured target.
    """
    return _run(
        "read_account_rate_limits",
        payload.target,
        lambda client: client.request("account/rateLimits/read", {}),
    )


def read_account_usage(payload: AccountUsageRequest) -> Response[RpcEnvelope]:
    """Read account usage or a thread-scoped native usage estimate.

    Args:
        payload: Target and optional exact thread/CWD identity.
    """
    params = {}
    if payload.thread_id is not None:
        thread_id = _action_id(payload.thread_id, "thread_id")
        cwd = _action_cwd(payload.cwd)
        params["threadId"] = thread_id

        def invoke(client):
            _read_guarded_thread(client, thread_id, cwd)
            return client.request("account/usage/read", params)

    else:

        def invoke(client):
            return client.request("account/usage/read", params)

    return _run("read_account_usage", payload.target, invoke)


def list_models(payload: ModelListRequest) -> Response[RpcEnvelope]:
    """List the native model catalog without changing thread configuration.

    Args:
        payload: Target and optional bounded model-catalog page options.
    """
    params: dict[str, Any] = {}
    if payload.limit is not None:
        params["limit"] = payload.limit
    if payload.cursor is not None:
        params["cursor"] = payload.cursor
    if payload.include_hidden is not None:
        params["includeHidden"] = payload.include_hidden
    return _run(
        "model/list",
        payload.target,
        lambda client: client.request("model/list", params),
    )


def read_model_provider_capabilities(
    payload: ModelProviderCapabilitiesRequest,
) -> Response[RpcEnvelope]:
    """Read native provider capabilities for the selected target.

    Args:
        payload: Configured target whose native provider capabilities are read.
    """
    return _run(
        "modelProvider/capabilities/read",
        payload.target,
        lambda client: client.request("modelProvider/capabilities/read", {}),
    )


def read_server_diagnostics(payload: ServerDiagnosticsRequest) -> Response[RpcEnvelope]:
    """Read bounded native server process and gauge diagnostics.

    Args:
        payload: Configured target whose bounded native diagnostics are read.
    """
    return _run(
        "server/diagnostics",
        payload.target,
        lambda client: client.request("server/diagnostics", {}),
    )
