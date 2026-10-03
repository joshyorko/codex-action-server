"""Action Server entrypoints for a bounded Codex App Server RPC surface.

These are ordinary Action Server actions. Action Server owns the MCP endpoint;
this package does not start an MCP server or expose a shell command action.
"""

from __future__ import annotations

from typing import Any, Literal
from contextlib import nullcontext
import os
from pathlib import Path
import subprocess
from websockets.exceptions import ConnectionClosed
import dispatch_receipts

from pydantic import BaseModel, ConfigDict, Field, model_validator
from actions import ActionError, Response, action

from boundary import configurations, resolve_target, validate_cwd, validate_identifier
from codex_rpc import Client, RpcError

TargetName = str  # Runtime operator allowlist is authoritative, not baked-in hostnames.
SortDirection = Literal["asc", "desc"]
TurnItemsView = Literal["notLoaded", "summary", "full"]
ThreadGoalStatus = Literal[
    "active", "paused", "blocked", "usageLimited", "budgetLimited", "complete"
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ThreadListRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str | None = Field(
        default=None, description="Optional exact absolute worktree path"
    )
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = None


class ThreadReadRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str
    thread_id: str
    include_turns: bool = False


class ThreadSettingsFields(StrictModel):
    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=r"^\S+$",
        description="Optional native model override; omission preserves the current model.",
    )
    model_provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=r"^\S+$",
        description="Optional native model provider; omission preserves the current provider.",
    )
    effort: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^\S+$",
        description="Optional native reasoning effort; omission preserves the current effort.",
    )


class ThreadStartRequest(ThreadSettingsFields):
    request_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
        description="Persist one client-generated ID before dispatch. Reuse it only with identical arguments; retries return the receipt and never dispatch again.",
    )
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str


class CreateThreadAndStartTurnRequest(ThreadSettingsFields):
    request_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
        description="Persist one client-generated ID before dispatch. Reuse it only with identical arguments; retries return the receipt and never dispatch again.",
    )
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str
    text: str = Field(min_length=1)
    wait_for_completion: bool = False
    wait_seconds: float = Field(default=15, gt=0, le=30)
    enable_list_threads_callback: bool = Field(
        default=False,
        strict=True,
        description="Register only codex_app.list_threads on this connection; requires wait_for_completion.",
    )

    @model_validator(mode="after")
    def require_callback_wait(self):
        if self.enable_list_threads_callback and not self.wait_for_completion:
            raise ValueError(
                "enable_list_threads_callback requires wait_for_completion"
            )
        return self


class ThreadResumeRequest(ThreadSettingsFields):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str
    thread_id: str


class TurnStartRequest(StrictModel):
    request_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
        description="Persist one client-generated ID before dispatch. Reuse it only with identical arguments; retries return the receipt and never dispatch again.",
    )
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str
    thread_id: str
    text: str = Field(min_length=1)
    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=r"^\S+$",
        description="Optional native model override; omission preserves the thread model and provider.",
    )
    effort: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^\S+$",
        description="Optional native reasoning effort, for example max; omission preserves existing settings.",
    )
    wait_for_completion: bool = False
    wait_seconds: float = Field(default=15, gt=0, le=30)


class TurnSteerRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str
    thread_id: str
    turn_id: str
    text: str = Field(min_length=1)


class TurnInterruptRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str
    thread_id: str
    turn_id: str


class ThreadPageRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str
    thread_id: str
    limit: int | None = Field(default=None, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1)
    sort_direction: SortDirection | None = None


class ThreadTurnsListRequest(ThreadPageRequest):
    items_view: TurnItemsView | None = None


class ThreadItemsListRequest(ThreadPageRequest):
    turn_id: str | None = None


class ThreadSettingsUpdateRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str
    thread_id: str
    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=r"^\S+$",
    )
    effort: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^\S+$",
    )

    @model_validator(mode="after")
    def require_a_setting(self):
        if self.model is None and self.effort is None:
            raise ValueError("at least one of model or effort is required")
        return self


class TurnSettingsUpdateRequest(ThreadSettingsUpdateRequest):
    turn_id: str


class ThreadGoalGetRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str
    thread_id: str


class ThreadGoalSetRequest(ThreadGoalGetRequest):
    objective: str | None = Field(default=None, min_length=1, max_length=4096)
    status: ThreadGoalStatus | None = None
    token_budget: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_a_goal_change(self):
        if self.objective is None and self.status is None and self.token_budget is None:
            raise ValueError("at least one goal field is required")
        return self


class ThreadGoalClearRequest(ThreadGoalGetRequest):
    pass


class ModelListRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    limit: int | None = Field(default=None, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1)
    include_hidden: bool | None = None


class ModelProviderCapabilitiesRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )


class ServerDiagnosticsRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )


class McpServerStatusListRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    thread_id: str | None = None
    limit: int | None = Field(default=None, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1)
    detail: Literal["full", "toolsAndAuthOnly"] | None = None


class LoadedThreadListRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    limit: int | None = Field(default=None, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1)


class EffectiveConfiguration(StrictModel):
    model: str | None = None
    model_provider: str | None = None
    effort: str | None = None


class ConnectionInfo(StrictModel):
    target: str
    codex_bin: str
    socket: str | None = None
    codex_home: str | None = None
    server: str | None = None


class RpcEnvelope(StrictModel):
    operation: str
    connection: ConnectionInfo
    result: dict[str, Any]
    effective_configuration: EffectiveConfiguration | None = None
    receipts: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)


def _action_cwd(cwd: str) -> str:
    try:
        return validate_cwd(cwd)
    except ValueError as error:
        raise ActionError(str(error)) from error


def _action_id(value: str, field_name: str) -> str:
    try:
        return validate_identifier(value, field_name)
    except ValueError as error:
        raise ActionError(str(error)) from error


def _read_guarded_thread(
    client: Client, thread_id: str, cwd: str, *, include_turns: bool = False
) -> dict[str, Any]:
    actual = client.request(
        "thread/read", {"threadId": thread_id, "includeTurns": include_turns}
    )
    thread = actual.get("thread") if isinstance(actual, dict) else None
    if (
        not isinstance(thread, dict)
        or thread.get("id") != thread_id
        or thread.get("cwd") != cwd
    ):
        raise ActionError(
            "Stored thread id/cwd does not match the requested workstream"
        )
    return actual


def _guard_cwd(client: Client, thread_id: str, cwd: str) -> None:
    _read_guarded_thread(client, thread_id, cwd)


def _optional_thread_settings(payload) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if payload.model is not None:
        params["model"] = payload.model
    if payload.model_provider is not None:
        params["modelProvider"] = payload.model_provider
    return params


def _optional_page_params(payload) -> dict[str, Any]:
    params: dict[str, Any] = {"threadId": payload.thread_id}
    if payload.limit is not None:
        params["limit"] = payload.limit
    if payload.cursor is not None:
        params["cursor"] = payload.cursor
    if payload.sort_direction is not None:
        params["sortDirection"] = payload.sort_direction
    return params


def _effective_configuration(result: dict[str, Any]) -> EffectiveConfiguration | None:
    if not isinstance(result, dict):
        return None
    thread = result.get("thread")
    sources = [result]
    if isinstance(thread, dict):
        sources.append(thread)
    native = result.get("native")
    if isinstance(native, dict):
        # The effort-only update cannot change model/provider. Retain those
        # observations, but never promote pre-update effort to current effort.
        sources.append(
            {key: native[key] for key in ("model", "modelProvider") if key in native}
        )
    values = {
        "model": next(
            (source["model"] for source in sources if "model" in source), None
        ),
        "model_provider": next(
            (
                source["modelProvider"]
                for source in sources
                if "modelProvider" in source
            ),
            None,
        ),
        "effort": next(
            (
                source["reasoningEffort"]
                for source in sources
                if "reasoningEffort" in source
            ),
            None,
        ),
    }
    return (
        EffectiveConfiguration(**values)
        if any(value is not None for value in values.values())
        else None
    )


def _envelope(operation: str, client: Client, result: dict[str, Any]) -> RpcEnvelope:
    provenance = client.provenance()
    return RpcEnvelope(
        operation=operation,
        connection=ConnectionInfo(
            target=provenance.get("target", ""),
            codex_bin=provenance.get("codexBin", ""),
            socket=provenance.get("socket"),
            codex_home=provenance.get("codexHome"),
            server=provenance.get("server"),
        ),
        result=result,
        effective_configuration=_effective_configuration(result),
        receipts=client.receipts,
        events=client.events,
    )


def _run(operation: str, target_name: str, callback) -> Response[RpcEnvelope]:
    try:
        target = resolve_target(target_name)
        with Client(target) as client:
            result = callback(client)
            return Response(result=_envelope(operation, client, result))
    except ActionError:
        raise
    except (
        RpcError,
        OSError,
        TimeoutError,
        ValueError,
        KeyError,
        subprocess.SubprocessError,
        ConnectionClosed,
    ) as error:
        raise ActionError(
            f"{operation} failed closed ({type(error).__name__}); inspect target diagnostics"
        ) from None


class _UnkeyedReceipt:
    replayed = False

    def __init__(self):
        self.data = {"state": "unknown", "request_id": None}

    def update(self, **values):
        self.data.update(values)

    def public(self):
        return {
            **self.data,
            "replayed": False,
            "retry_same_request_id": "unsafe_without_request_id",
            "native_state_authoritative": True,
        }


def _receipt_root():
    configured = os.environ.get("CODEX_ACTION_RECEIPTS")
    return (
        Path(configured)
        if configured
        else Path.home() / ".local/state/codex-action-server/receipts"
    )


def _dispatch_run(operation, payload, callback):
    # Reserve before connecting. A transport failure cannot trigger implicit replay.
    scope = (
        dispatch_receipts.reserve(
            _receipt_root(),
            payload.request_id,
            {"operation": operation, **payload.model_dump()},
        )
        if payload.request_id
        else nullcontext(_UnkeyedReceipt())
    )
    try:
        with scope as receipt:
            if receipt.replayed:
                return Response(
                    result=RpcEnvelope(
                        operation=operation,
                        connection=ConnectionInfo(target=payload.target, codex_bin=""),
                        result={"dispatch": receipt.public()},
                    )
                )
            client = None
            try:
                target = resolve_target(payload.target)
                with Client(target) as client:
                    receipt.update(
                        wait_mode="bounded"
                        if getattr(payload, "wait_for_completion", False)
                        else "dispatch"
                    )
                    result = callback(client, receipt)
                    result["dispatch"] = receipt.public()
                    return Response(result=_envelope(operation, client, result))
            except (
                ActionError,
                RpcError,
                OSError,
                TimeoutError,
                ValueError,
                KeyError,
                subprocess.SubprocessError,
                ConnectionClosed,
            ) as error:
                receipt.update(error_code=type(error).__name__)
                envelope = RpcEnvelope(
                    operation=operation,
                    connection=ConnectionInfo(target=payload.target, codex_bin=""),
                    result={"dispatch": receipt.public()},
                )
                return Response(result=envelope)
    except (ValueError, OSError) as error:
        raise ActionError(
            f"dispatch receipt rejected ({type(error).__name__})"
        ) from None


class DispatchReceiptRequest(StrictModel):
    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@action(is_consequential=False)
def read_dispatch_receipt(payload: DispatchReceiptRequest) -> Response[dict[str, Any]]:
    """Read a dispatch acknowledgement; native Codex owns current turn status.

    Args:
        payload: The exact client-generated request ID.
    """
    return Response(result=dispatch_receipts.read(_receipt_root(), payload.request_id))


@action(is_consequential=False)
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


@action(is_consequential=False)
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


def _apply_thread_effort(
    client: Client,
    native_result: dict[str, Any],
    thread_id: str,
    cwd: str,
    effort: str | None,
) -> dict[str, Any]:
    if effort is None:
        return native_result
    settings = client.request(
        "thread/settings/update", {"threadId": thread_id, "effort": effort}
    )
    current = _read_guarded_thread(client, thread_id, cwd)
    return {
        "native": native_result,
        "settings": settings,
        "thread": current["thread"],
    }


@action(is_consequential=False)
def discover_threads(payload: ThreadListRequest) -> Response[RpcEnvelope]:
    """Discover persisted threads on one configured Codex target.

    Args:
        payload: Target and optional exact cwd/list pagination controls.
    """
    params: dict[str, Any] = {"limit": payload.limit}
    if payload.cwd is not None:
        params["cwd"] = _action_cwd(payload.cwd)
    if payload.cursor is not None:
        params["cursor"] = payload.cursor
    return _run(
        "thread/list",
        payload.target,
        lambda client: client.request("thread/list", params),
    )


@action(is_consequential=False)
def list_thread_turns(payload: ThreadTurnsListRequest) -> Response[RpcEnvelope]:
    """Read one page of turns for an exact target-scoped workstream thread.

    Args:
        payload: Target, exact cwd/thread, and bounded turn-page options.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")
    params = _optional_page_params(payload)
    if payload.items_view is not None:
        params["itemsView"] = payload.items_view

    def invoke(client):
        _guard_cwd(client, thread_id, cwd)
        return client.request("thread/turns/list", params)

    return _run("thread/turns/list", payload.target, invoke)


@action(is_consequential=False)
def list_thread_items(payload: ThreadItemsListRequest) -> Response[RpcEnvelope]:
    """Read one page of items for an exact target-scoped workstream thread.

    Args:
        payload: Target, exact cwd/thread, optional turn, and item-page options.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")
    params = _optional_page_params(payload)
    if payload.turn_id is not None:
        params["turnId"] = _action_id(payload.turn_id, "turn_id")

    def invoke(client):
        _guard_cwd(client, thread_id, cwd)
        return client.request("thread/items/list", params)

    return _run("thread/items/list", payload.target, invoke)


@action(is_consequential=False)
def read_thread(payload: ThreadReadRequest) -> Response[RpcEnvelope]:
    """Read one target-scoped thread without changing it.

    Args:
        payload: Target, exact worktree cwd, and thread identifier.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")

    def invoke(client):
        result = client.request(
            "thread/read",
            {
                "threadId": thread_id,
                "includeTurns": payload.include_turns,
            },
        )
        thread = result.get("thread") if isinstance(result, dict) else None
        if (
            not isinstance(thread, dict)
            or thread.get("id") != thread_id
            or thread.get("cwd") != cwd
        ):
            raise ActionError(
                "Stored thread id/cwd does not match the requested workstream"
            )
        return result

    return _run("thread/read", payload.target, invoke)


@action(is_consequential=True)
def update_thread_settings(
    payload: ThreadSettingsUpdateRequest,
) -> Response[RpcEnvelope]:
    """Update only model or reasoning effort for subsequent thread turns.

    Args:
        payload: Target, exact cwd/thread, and model or effort for later turns.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")

    def invoke(client):
        _guard_cwd(client, thread_id, cwd)
        params: dict[str, Any] = {"threadId": thread_id}
        if payload.model is not None:
            params["model"] = payload.model
        if payload.effort is not None:
            params["effort"] = payload.effort
        updated = client.request("thread/settings/update", params)
        current = _read_guarded_thread(client, thread_id, cwd)
        return {"updated": updated, "thread": current["thread"]}

    return _run("thread/settings/update", payload.target, invoke)


@action(is_consequential=True)
def update_turn_settings(payload: TurnSettingsUpdateRequest) -> Response[RpcEnvelope]:
    """Apply native model or effort settings to one explicitly active turn.

    Args:
        payload: Target, exact cwd/thread/active-turn, and model or effort.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")
    turn_id = _action_id(payload.turn_id, "turn_id")

    def invoke(client):
        _guard_cwd(client, thread_id, cwd)
        params: dict[str, Any] = {"threadId": thread_id, "turnId": turn_id}
        if payload.model is not None:
            params["model"] = payload.model
        if payload.effort is not None:
            params["effort"] = payload.effort
        return client.request("turn/settings/update", params)

    return _run("turn/settings/update", payload.target, invoke)


def _goal_result(
    result: dict[str, Any], thread_id: str, *, required: bool = False
) -> dict[str, Any]:
    goal = result.get("goal") if isinstance(result, dict) else None
    if (required and not isinstance(goal, dict)) or (
        goal is not None
        and (not isinstance(goal, dict) or goal.get("threadId") != thread_id)
    ):
        raise ActionError("Native goal thread id does not match the requested thread")
    return result


@action(is_consequential=False)
def get_thread_goal(payload: ThreadGoalGetRequest) -> Response[RpcEnvelope]:
    """Read the native coordinator goal for one exact workstream thread.

    Args:
        payload: Target and exact worktree cwd/thread whose goal is read.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")

    def invoke(client):
        _guard_cwd(client, thread_id, cwd)
        return _goal_result(
            client.request("thread/goal/get", {"threadId": thread_id}), thread_id
        )

    return _run("thread/goal/get", payload.target, invoke)


@action(is_consequential=True)
def set_thread_goal(payload: ThreadGoalSetRequest) -> Response[RpcEnvelope]:
    """Set bounded native coordinator-goal fields for one exact thread.

    Args:
        payload: Target, exact cwd/thread, and objective, status, or token budget.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")

    def invoke(client):
        _guard_cwd(client, thread_id, cwd)
        params: dict[str, Any] = {"threadId": thread_id}
        if payload.objective is not None:
            params["objective"] = payload.objective
        if payload.status is not None:
            params["status"] = payload.status
        if payload.token_budget is not None:
            params["tokenBudget"] = payload.token_budget
        return _goal_result(
            client.request("thread/goal/set", params), thread_id, required=True
        )

    return _run("thread/goal/set", payload.target, invoke)


@action(is_consequential=True)
def clear_thread_goal(payload: ThreadGoalClearRequest) -> Response[RpcEnvelope]:
    """Clear the native coordinator goal for one exact workstream thread.

    Args:
        payload: Target and exact worktree cwd/thread whose goal is cleared.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")

    def invoke(client):
        _guard_cwd(client, thread_id, cwd)
        return client.request("thread/goal/clear", {"threadId": thread_id})

    return _run("thread/goal/clear", payload.target, invoke)


@action(is_consequential=False)
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


@action(is_consequential=False)
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


@action(is_consequential=False)
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


@action(is_consequential=False)
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


@action(is_consequential=False)
def list_loaded_threads(payload: LoadedThreadListRequest) -> Response[RpcEnvelope]:
    """List native thread ids currently loaded in memory.

    Args:
        payload: Target and optional bounded loaded-thread page options.
    """
    params: dict[str, Any] = {}
    if payload.limit is not None:
        params["limit"] = payload.limit
    if payload.cursor is not None:
        params["cursor"] = payload.cursor
    return _run(
        "thread/loaded/list",
        payload.target,
        lambda client: client.request("thread/loaded/list", params),
    )


@action(is_consequential=True)
def start_thread(payload: ThreadStartRequest) -> Response[RpcEnvelope]:
    """Create a native thread rooted at the explicit absolute worktree path.

    Args:
        payload: Target and explicit absolute worktree cwd.
    """
    cwd = _action_cwd(payload.cwd)

    def invoke(client, receipt):
        params = {"cwd": cwd, **_optional_thread_settings(payload)}
        result = client.request("thread/start", params)
        thread = result.get("thread") if isinstance(result, dict) else None
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise ActionError("Native thread/start returned no thread id")
        receipt.update(thread_id=thread_id)
        client.set_workstream(cwd, thread_id)
        receipt.update(state="created_not_materialized")
        return _apply_thread_effort(client, result, thread_id, cwd, payload.effort)

    return _dispatch_run("thread/start", payload, invoke)


@action(is_consequential=True)
def create_thread_and_start_turn(
    payload: CreateThreadAndStartTurnRequest,
) -> Response[RpcEnvelope]:
    """Create a fresh thread and start its first typed turn on one connection.

    A new Codex 0.153.4 thread has no resumable rollout until its first turn;
    this action keeps creation and that first turn on the native connection.

    Args:
        payload: Target, exact cwd, first input, optional settings, and wait flag.
    """
    cwd = _action_cwd(payload.cwd)

    def invoke(client, receipt):
        params = {"cwd": cwd, **_optional_thread_settings(payload)}
        if payload.enable_list_threads_callback:
            params["dynamicTools"] = [
                {
                    "type": "namespace",
                    "name": "codex_app",
                    "description": "Read-only native state for this exact workstream.",
                    "tools": [
                        {
                            "type": "function",
                            "name": "list_threads",
                            "description": "List native threads filtered to this thread's exact cwd.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "limit": {
                                        "type": "integer",
                                        "minimum": 1,
                                        "maximum": 100,
                                    },
                                    "cursor": {"type": "string", "minLength": 1},
                                    "cwd": {"type": "string", "enum": [cwd]},
                                },
                                "required": ["cwd"],
                                "additionalProperties": False,
                            },
                        }
                    ],
                }
            ]
        created = client.request("thread/start", params)
        thread = created.get("thread") if isinstance(created, dict) else None
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise ActionError("Native thread/start returned no thread id")
        receipt.update(thread_id=thread_id)
        client.set_workstream(cwd, thread_id)
        client.ensure_thread_attached(
            thread_id,
            {"threadId": thread_id, "excludeTurns": True, "cwd": cwd},
        )
        turn_params: dict[str, Any] = {
            "threadId": thread_id,
            "cwd": cwd,
            "input": [{"type": "text", "text": payload.text}],
        }
        if payload.effort is not None:
            turn_params["effort"] = payload.effort
        accepted = client.request("turn/start", turn_params)
        turn_id = accepted.get("turn", {}).get("id")
        if not isinstance(turn_id, str) or not turn_id:
            raise RpcError("Native turn acknowledgement missing ID")
        receipt.update(state="accepted", thread_id=thread_id, turn_id=turn_id)
        result: dict[str, Any] = {"created": created, "accepted": accepted}
        for native_key in ("model", "modelProvider"):
            if native_key in created:
                result[native_key] = created[native_key]
        if not payload.wait_for_completion:
            return result
        turn = accepted.get("turn") if isinstance(accepted, dict) else None
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(turn_id, str) or not turn_id:
            raise ActionError("Native turn/start returned no turn id")
        result["completed"] = client.wait_turn(
            thread_id, turn_id, timeout=payload.wait_seconds
        )
        receipt.update(state=result["completed"].get("status", "completed"))
        current = _read_guarded_thread(client, thread_id, cwd)
        result["thread"] = current["thread"]
        return result

    return _dispatch_run("thread/start + turn/start", payload, invoke)


@action(is_consequential=True)
def resume_thread(payload: ThreadResumeRequest) -> Response[RpcEnvelope]:
    """Attach the current native connection to an existing thread after cwd verification.

    Args:
        payload: Target, exact worktree cwd, and thread identifier.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")

    def invoke(client):
        client.set_workstream(cwd, thread_id)
        _guard_cwd(client, thread_id, cwd)
        params: dict[str, Any] = {
            "threadId": thread_id,
            "cwd": cwd,
            "excludeTurns": True,
            **_optional_thread_settings(payload),
        }
        result = client.request(
            "thread/resume",
            params,
        )
        return _apply_thread_effort(client, result, thread_id, cwd, payload.effort)

    return _run("thread/resume", payload.target, invoke)


@action(is_consequential=True)
def start_turn(payload: TurnStartRequest) -> Response[RpcEnvelope]:
    """Resume, then start a text turn on the same native connection.

    Optional model and effort override native turn settings; the provider and
    execution policy are preserved. Omit them to retain the existing settings.

    Args:
        payload: Target, exact cwd, thread, text, and optional model/effort.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")

    def invoke(client, receipt):
        receipt.update(thread_id=thread_id)
        client.set_workstream(cwd, thread_id)
        _guard_cwd(client, thread_id, cwd)
        client.request("thread/resume", {"threadId": thread_id, "excludeTurns": True})
        params: dict[str, Any] = {
            "threadId": thread_id,
            "cwd": cwd,
            "input": [{"type": "text", "text": payload.text}],
        }
        if payload.model is not None:
            params["model"] = payload.model
        if payload.effort is not None:
            params["effort"] = payload.effort
        accepted = client.request("turn/start", params)
        turn_id = accepted.get("turn", {}).get("id")
        if not isinstance(turn_id, str) or not turn_id:
            raise RpcError("Native turn acknowledgement missing ID")
        receipt.update(state="accepted", thread_id=thread_id, turn_id=turn_id)
        if not payload.wait_for_completion:
            return accepted
        turn_id = accepted["turn"]["id"]
        completed = client.wait_turn(thread_id, turn_id, timeout=payload.wait_seconds)
        receipt.update(state=completed.get("status", "completed"))
        return {"accepted": accepted, "completed": completed}

    return _dispatch_run("turn/start", payload, invoke)


@action(is_consequential=True)
def steer_turn(payload: TurnSteerRequest) -> Response[RpcEnvelope]:
    """Steer only the explicitly named active turn after cwd verification.

    Args:
        payload: Target, exact worktree cwd, thread/turn identifiers, and steer text.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")
    turn_id = _action_id(payload.turn_id, "turn_id")

    def invoke(client):
        _guard_cwd(client, thread_id, cwd)
        return client.request(
            "turn/steer",
            {
                "threadId": thread_id,
                "expectedTurnId": turn_id,
                "input": [{"type": "text", "text": payload.text}],
            },
        )

    return _run("turn/steer", payload.target, invoke)


@action(is_consequential=True)
def interrupt_turn(payload: TurnInterruptRequest) -> Response[RpcEnvelope]:
    """Interrupt only the explicitly named active turn after cwd verification.

    Args:
        payload: Target, exact worktree cwd, and thread/turn identifiers.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")
    turn_id = _action_id(payload.turn_id, "turn_id")

    def invoke(client):
        _guard_cwd(client, thread_id, cwd)
        return client.request(
            "turn/interrupt",
            {
                "threadId": thread_id,
                "turnId": turn_id,
            },
        )

    return _run("turn/interrupt", payload.target, invoke)
