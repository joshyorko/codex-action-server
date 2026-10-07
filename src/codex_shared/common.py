"""Shared common behavior; importing this module registers no actions."""

from __future__ import annotations

from actions import ActionError
from typing import Any
from codex_rpc import Client
from websockets.exceptions import ConnectionClosed
from codex_shared.models import ConnectionInfo
from codex_shared.models import EffectiveConfiguration
from pathlib import Path
from actions import Response
from codex_shared.models import RpcEnvelope
from codex_rpc import RpcError
import dispatch_receipts
import json
from contextlib import nullcontext
import os
from boundary import resolve_target
import subprocess
from boundary import validate_cwd
from boundary import validate_identifier


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


def _bounded_native_result(result: dict[str, Any], maximum_bytes: int = 1_048_576):
    try:
        encoded = json.dumps(result, ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise RpcError("Native result is not bounded JSON data") from error
    if len(encoded) > maximum_bytes:
        raise RpcError("Native result exceeds the 1 MiB action limit")
    return result


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


def _envelope(
    operation: str,
    client: Client,
    result: dict[str, Any],
    *,
    include_receipts: bool = True,
    include_events: bool = True,
) -> RpcEnvelope:
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
        receipts=client.receipts if include_receipts else [],
        events=client.events if include_events else [],
    )


def _run(
    operation: str,
    target_name: str,
    callback,
    *,
    include_receipts: bool = True,
    include_events: bool = True,
    maximum_response_bytes: int | None = None,
) -> Response[RpcEnvelope]:
    try:
        target = resolve_target(target_name)
        with Client(target) as client:
            result = callback(client)
            response = Response(
                result=_envelope(
                    operation,
                    client,
                    result,
                    include_receipts=include_receipts,
                    include_events=include_events,
                )
            )
            if maximum_response_bytes is not None:
                encoded = json.dumps(
                    response.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                if len(encoded) > maximum_response_bytes:
                    raise RpcError("Native supervisory snapshot exceeds its byte limit")
            return response
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
