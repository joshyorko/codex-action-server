"""Shared queues behavior; importing this module registers no actions."""

from __future__ import annotations

from typing import Any
from actions import Response
from codex_shared.models import RpcEnvelope
from codex_rpc import RpcError
from codex_shared.models import ThreadMutationRequest
from codex_shared.models import ThreadQueueAddRequest
from codex_shared.models import ThreadQueueDeleteRequest
from codex_shared.models import ThreadQueueListRequest
from codex_shared.models import ThreadQueueReorderRequest
from codex_shared.models import ThreadQueueStartRequest
from codex_shared.models import ThreadQueueUpdateRequest
from codex_shared.common import _action_cwd
from codex_shared.common import _action_id
from codex_shared.common import _dispatch_run
from codex_shared.common import _read_guarded_thread
from codex_shared.common import _run


def _thread_queue_control(
    operation: str,
    payload: ThreadMutationRequest,
    method: str,
    params: dict[str, Any],
    *,
    expected_submission_id: str | None = None,
    starts_turn: bool = False,
) -> Response[RpcEnvelope]:
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)

    def invoke(client, receipt):
        _read_guarded_thread(client, thread_id, cwd)
        result = client.request(method, {"threadId": thread_id, **params})
        if expected_submission_id is not None:
            queued = (
                result.get("queuedSubmission") if isinstance(result, dict) else None
            )
            if (
                not isinstance(queued, dict)
                or queued.get("id") != expected_submission_id
            ):
                raise RpcError("Native queued submission identity mismatch")
        elif method == "thread/queue/add":
            queued = (
                result.get("queuedSubmission") if isinstance(result, dict) else None
            )
            if not isinstance(queued, dict) or not isinstance(queued.get("id"), str):
                raise RpcError("Native queued submission response is missing its ID")
        if method == "thread/queue/delete" and (
            not isinstance(result, dict) or result.get("deleted") is not True
        ):
            raise RpcError("Native queue delete did not confirm deletion")
        if starts_turn:
            turn = result.get("turn") if isinstance(result, dict) else None
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            if not isinstance(turn_id, str) or not turn_id:
                raise RpcError("Native queued turn response is missing its turn ID")
            receipt.update(state="accepted", thread_id=thread_id, turn_id=turn_id)
        else:
            receipt.update(state="accepted", thread_id=thread_id)
        return result

    return _dispatch_run(operation, payload, invoke)


def list_thread_queue(payload: ThreadQueueListRequest) -> Response[RpcEnvelope]:
    """List a bounded native queue page for one exact thread.

    Args:
        payload: Exact target, cwd, thread ID, and page controls.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    params = {"threadId": thread_id, "limit": payload.limit}
    if payload.cursor is not None:
        params["cursor"] = payload.cursor

    def invoke(client):
        _read_guarded_thread(client, thread_id, cwd)
        return client.request("thread/queue/list", params)

    return _run("list_thread_queue", payload.target, invoke)


def add_thread_queue_item(payload: ThreadQueueAddRequest) -> Response[RpcEnvelope]:
    """Add one bounded text submission to the exact native thread queue.

    Args:
        payload: Exact thread identity, client message ID, text, and receipt key.
    """
    return _thread_queue_control(
        "add_thread_queue_item",
        payload,
        "thread/queue/add",
        {
            "input": [{"type": "text", "text": payload.text}],
            "clientUserMessageId": payload.client_user_message_id,
        },
    )


def update_thread_queue_item(
    payload: ThreadQueueUpdateRequest,
) -> Response[RpcEnvelope]:
    """Replace the typed text input for one exact queued submission.

    Args:
        payload: Exact thread and queue item identities, text, and receipt key.
    """
    return _thread_queue_control(
        "update_thread_queue_item",
        payload,
        "thread/queue/update",
        {
            "queuedSubmissionId": payload.queued_submission_id,
            "input": [{"type": "text", "text": payload.text}],
        },
        expected_submission_id=payload.queued_submission_id,
    )


def delete_thread_queue_item(
    payload: ThreadQueueDeleteRequest,
) -> Response[RpcEnvelope]:
    """Delete one exact queued submission.

    Args:
        payload: Exact thread and queue item identities, plus receipt key.
    """
    return _thread_queue_control(
        "delete_thread_queue_item",
        payload,
        "thread/queue/delete",
        {"queuedSubmissionId": payload.queued_submission_id},
    )


def reorder_thread_queue(
    payload: ThreadQueueReorderRequest,
) -> Response[RpcEnvelope]:
    """Set the order of a bounded set of unique queued submission IDs.

    Args:
        payload: Exact thread identity and an ordered, unique ID list.
    """
    return _thread_queue_control(
        "reorder_thread_queue",
        payload,
        "thread/queue/reorder",
        {"queuedSubmissionIds": payload.queued_submission_ids},
    )


def start_thread_queue(payload: ThreadQueueStartRequest) -> Response[RpcEnvelope]:
    """Start one exact queued submission or the native queue head.

    Args:
        payload: Exact thread identity and optional queued item ID.
    """
    params = (
        {"queuedSubmissionId": payload.queued_submission_id}
        if payload.queued_submission_id is not None
        else {}
    )
    return _thread_queue_control(
        "start_thread_queue",
        payload,
        "thread/queue/start",
        params,
        starts_turn=True,
    )
