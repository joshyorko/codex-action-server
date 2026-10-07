"""Shared threads behavior; importing this module registers no actions."""

from __future__ import annotations

from actions import ActionError
from typing import Any
from codex_shared.models import LoadedThreadListRequest
from actions import Response
from codex_shared.models import RpcEnvelope
from codex_rpc import RpcError
from codex_shared.models import ThreadItemsListRequest
from codex_shared.models import ThreadListRequest
from codex_shared.models import ThreadReadRequest
from codex_shared.models import ThreadSearchOccurrencesRequest
from codex_shared.models import ThreadSearchRequest
from codex_shared.models import ThreadSectionListRequest
from codex_shared.models import ThreadSnapshotRequest
from codex_shared.models import ThreadTimelineListRequest
from codex_shared.models import ThreadTurnsListRequest
from codex_shared.common import _action_cwd
from codex_shared.common import _action_id
from codex_shared.common import _guard_cwd
from codex_shared.common import _optional_page_params
from codex_shared.common import _read_guarded_thread
from codex_shared.common import _run
from datetime import datetime
import hashlib
import json
from datetime import timezone


SNAPSHOT_MAX_BYTES = 8192

SNAPSHOT_MESSAGE_MAX_BYTES = 512

_THREAD_STATUS_TYPES = {"active", "idle", "notLoaded", "systemError"}

_TURN_STATUS_TYPES = {"completed", "failed", "inProgress", "interrupted"}

_ACTIVE_FLAGS = {"waitingOnApproval", "waitingOnUserInput"}

_ERROR_CODES = {
    "activeTurnNotSteerable",
    "badRequest",
    "contextWindowExceeded",
    "cyberPolicy",
    "flexUnavailable",
    "internalServerError",
    "misalignmentPolicyViolation",
    "other",
    "rateLimitExceeded",
    "sandboxError",
    "serverOverloaded",
    "sessionBudgetExceeded",
    "threadRollbackFailed",
    "tooManyDenials",
    "unauthorized",
    "usageLimitExceeded",
}


def _utf8_truncate(value: str, maximum_bytes: int) -> tuple[str, bool]:
    if maximum_bytes < len("…".encode("utf-8")):
        return "", bool(value)
    size = 0
    result = []
    for character in value:
        width = len(character.encode("utf-8"))
        if size + width > maximum_bytes:
            while result and size + len("…".encode("utf-8")) > maximum_bytes:
                size -= len(result.pop().encode("utf-8"))
            if size + len("…".encode("utf-8")) > maximum_bytes:
                return "", True
            return "".join(result) + "…", True
        result.append(character)
        size += width
    return "".join(result), False


def _snapshot_error_code(error: Any) -> str | None:
    if not isinstance(error, dict):
        return None
    code = error.get("codexErrorInfo")
    if isinstance(code, dict) and len(code) == 1:
        code = next(iter(code))
    if isinstance(code, str) and code in _ERROR_CODES:
        return code
    return "unclassified" if isinstance(error.get("message"), str) else None


def _snapshot_unknown_state_digest(value: Any) -> str:
    digest = hashlib.sha256(b"codex-action-server.snapshot-unknown-state.v1\0")
    encoder = json.JSONEncoder(
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    for chunk in encoder.iterencode(value):
        digest.update(chunk.encode("ascii"))
    return digest.hexdigest()


def _user_message_excerpt(content: list[Any]) -> tuple[str, bool]:
    excerpts = []
    remaining = SNAPSHOT_MESSAGE_MAX_BYTES
    truncated = False
    for part in content:
        if (
            not isinstance(part, dict)
            or part.get("type") != "text"
            or not isinstance(part.get("text"), str)
        ):
            continue
        prefix = "\n" if excerpts else ""
        remaining -= len(prefix.encode("utf-8"))
        if remaining <= 0:
            truncated = True
            break
        text, text_truncated = _utf8_truncate(part["text"], remaining)
        excerpts.append(prefix + text)
        remaining -= len(text.encode("utf-8"))
        if text_truncated:
            truncated = True
            break
    return "".join(excerpts), truncated


def _snapshot_item(item_entry: Any, turn_id: str) -> dict[str, Any] | None:
    if item_entry is None:
        return None
    if (
        not isinstance(item_entry, dict)
        or item_entry.get("turnId") != turn_id
        or not isinstance(item_entry.get("item"), dict)
    ):
        raise RpcError("Native snapshot item identity mismatch")
    item = item_entry["item"]
    kind = item.get("type")
    if not isinstance(kind, str) or len(kind) > 64:
        raise RpcError("Native snapshot item type is invalid")
    item_id = item.get("id")
    if not isinstance(item_id, str) or not item_id or len(item_id) > 512:
        raise RpcError("Native snapshot item identity is invalid")
    status = item.get("status")
    projected_status = status if isinstance(status, str) and len(status) <= 64 else None
    phase = item.get("phase")
    projected_phase = phase if isinstance(phase, str) and len(phase) <= 64 else None
    text = None
    truncated = False
    if kind == "agentMessage" and isinstance(item.get("text"), str):
        text = item["text"]
    elif kind == "userMessage" and isinstance(item.get("content"), list):
        text, truncated = _user_message_excerpt(item["content"])
    if text is not None and kind == "agentMessage":
        text, truncated = _utf8_truncate(text, SNAPSHOT_MESSAGE_MAX_BYTES)
    return {
        "id": item_id,
        "kind": kind,
        "phase": projected_phase,
        "status": projected_status,
        "text": text,
        "text_truncated": truncated,
    }


def _project_thread_snapshot(
    target: str,
    cwd: str,
    thread_id: str,
    thread: dict[str, Any],
    turn: dict[str, Any] | None,
    latest_item: Any,
    item_cursor: Any,
) -> dict[str, Any]:
    native_status = thread.get("status")
    status_type = native_status.get("type") if isinstance(native_status, dict) else None
    known_status = (
        status_type
        if isinstance(status_type, str) and status_type in _THREAD_STATUS_TYPES
        else "unknown"
    )
    flags = (
        native_status.get("activeFlags", []) if isinstance(native_status, dict) else []
    )
    active_flags_missing = known_status == "active" and (
        not isinstance(native_status, dict) or "activeFlags" not in native_status
    )
    active_flags_invalid = (
        isinstance(native_status, dict)
        and "activeFlags" in native_status
        and not isinstance(flags, list)
    )
    active_flags = (
        sorted(
            flag for flag in flags if isinstance(flag, str) and flag in _ACTIVE_FLAGS
        )
        if isinstance(flags, list)
        else []
    )
    unknown_flags = (
        [
            flag
            for flag in flags
            if not isinstance(flag, str) or flag not in _ACTIVE_FLAGS
        ]
        if isinstance(flags, list)
        else flags
    )
    unknown_active_flags = (
        active_flags_missing
        or active_flags_invalid
        or (
            isinstance(flags, list)
            and any(
                not isinstance(flag, str) or flag not in _ACTIVE_FLAGS for flag in flags
            )
        )
    )
    turn_id = turn.get("id") if isinstance(turn, dict) else None
    turn_status = turn.get("status") if isinstance(turn, dict) else None
    turn_status_unknown = isinstance(turn_id, str) and (
        not isinstance(turn_status, str) or turn_status not in _TURN_STATUS_TYPES
    )
    projected_turn = (
        {
            **{
                "id": turn_id,
                "status": "unknown" if turn_status_unknown else turn_status,
                "error_code": _snapshot_error_code(turn.get("error")),
            },
            **({"native_status_unknown": True} if turn_status_unknown else {}),
        }
        if isinstance(turn, dict)
        else None
    )
    unknown_native_state = {}
    if known_status == "unknown":
        unknown_native_state["thread_status"] = {
            "present": "status" in thread,
            "type_present": isinstance(native_status, dict) and "type" in native_status,
            "value": status_type if isinstance(native_status, dict) else native_status,
        }
    if unknown_active_flags:
        unknown_native_state["active_flags"] = {
            "present": isinstance(native_status, dict)
            and "activeFlags" in native_status,
            "value": unknown_flags,
        }
    if turn_status_unknown:
        unknown_native_state["turn_status"] = {
            "present": isinstance(turn, dict) and "status" in turn,
            "value": turn_status,
        }
    cursor = (
        item_cursor
        if isinstance(item_cursor, str) and len(item_cursor) <= 512
        else None
    )
    cursor_digest = None
    if isinstance(item_cursor, str) and cursor is None:
        digest = hashlib.sha256()
        for offset in range(0, len(item_cursor), 1024):
            digest.update(item_cursor[offset : offset + 1024].encode("utf-8"))
        cursor_digest = digest.hexdigest()
    updated_at = thread.get("updatedAt")
    return {
        "target": target,
        "cwd": cwd,
        "thread_id": thread_id,
        "source": "native-app-server",
        "thread_status": known_status,
        "active_flags": active_flags,
        "unknown_active_flags": unknown_active_flags,
        "native_updated_at": updated_at
        if isinstance(updated_at, int) and not isinstance(updated_at, bool)
        else None,
        "native_updated_at_unknown": not (
            isinstance(updated_at, int) and not isinstance(updated_at, bool)
        ),
        "latest_turn": projected_turn,
        "latest_item": _snapshot_item(latest_item, turn_id)
        if isinstance(turn_id, str)
        else None,
        "_unknown_state_digest": _snapshot_unknown_state_digest(unknown_native_state)
        if unknown_native_state
        else None,
        "continuation": {
            "turn_id": turn_id if isinstance(turn_id, str) else None,
            "item_cursor": cursor,
            "item_cursor_omitted": item_cursor is not None and cursor is None,
            "item_cursor_digest": cursor_digest,
        },
    }


def _snapshot_response(projected: dict[str, Any], observed_at: str) -> dict[str, Any]:
    unknown_state_digest = projected.pop("_unknown_state_digest", None)
    revision = hashlib.sha256(
        json.dumps(
            {
                "projection": projected,
                "unknown_state_digest": unknown_state_digest,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    result = {
        **projected,
        "observed_at": observed_at,
        "revision": revision,
        "changed": True,
        "native_state_authoritative": True,
    }
    if (
        len(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        > SNAPSHOT_MAX_BYTES
    ):
        raise RpcError("Native supervisory snapshot exceeds its byte limit")
    return result


def discover_threads(payload: ThreadListRequest) -> Response[RpcEnvelope]:
    """Discover persisted threads in one exact worktree on a configured target.

    Args:
        payload: Target, required exact absolute cwd, and bounded pagination controls.
    """
    params: dict[str, Any] = {
        "cwd": _action_cwd(payload.cwd),
        "limit": payload.limit,
    }
    if payload.cursor is not None:
        params["cursor"] = payload.cursor
    return _run(
        "thread/list",
        payload.target,
        lambda client: client.request("thread/list", params),
    )


def list_thread_sections(payload: ThreadSectionListRequest) -> Response[RpcEnvelope]:
    """List a bounded page of native thread sections for one configured target.

    Args:
        payload: Target and bounded pagination controls.
    """
    params = {}
    if payload.limit is not None:
        params["limit"] = payload.limit
    if payload.cursor is not None:
        params["cursor"] = payload.cursor
    return _run(
        "list_thread_sections",
        payload.target,
        lambda client: client.request("threadSection/list", params),
    )


def search_threads(payload: ThreadSearchRequest) -> Response[RpcEnvelope]:
    """Search a bounded page of threads on one target using the pinned experimental API.

    Args:
        payload: Target, non-empty search text, and bounded native search filters.
    """
    params: dict[str, Any] = {
        "searchTerm": payload.search_term,
        "limit": payload.limit,
    }
    for key, value in (
        ("cursor", payload.cursor),
        ("sortKey", payload.sort_key),
        ("sortDirection", payload.sort_direction),
        ("sourceKinds", payload.source_kinds),
        ("archived", payload.archived),
    ):
        if value is not None:
            params[key] = value

    cwd = _action_cwd(payload.cwd)

    def invoke(client):
        result = client.request("thread/search", params)
        page = result.get("data") if isinstance(result, dict) else None
        if not isinstance(page, list):
            raise RpcError("Native thread/search response is missing its data page")
        selected = []
        for entry in page:
            thread = entry.get("thread") if isinstance(entry, dict) else None
            if (
                not isinstance(thread, dict)
                or not isinstance(thread.get("id"), str)
                or not thread["id"]
                or not isinstance(thread.get("cwd"), str)
            ):
                raise RpcError("Native thread/search result is missing thread identity")
            if thread["cwd"] == cwd:
                selected.append(entry)
        return {
            "data": selected,
            "nextCursor": result.get("nextCursor"),
            "backwardsCursor": result.get("backwardsCursor"),
        }

    return _run(
        "search_threads",
        payload.target,
        invoke,
        include_receipts=False,
    )


def search_thread_occurrences(
    payload: ThreadSearchOccurrencesRequest,
) -> Response[RpcEnvelope]:
    """Find bounded visible-message matches within one exact thread.

    Args:
        payload: Exact target, cwd, thread ID, query, and pagination controls.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    params = {
        "threadId": thread_id,
        "searchTerm": payload.search_term,
        "limit": payload.limit,
    }
    if payload.cursor is not None:
        params["cursor"] = payload.cursor

    def invoke(client):
        _read_guarded_thread(client, thread_id, cwd)
        return client.request("thread/searchOccurrences", params)

    return _run("search_thread_occurrences", payload.target, invoke)


def list_thread_timeline(payload: ThreadTimelineListRequest) -> Response[RpcEnvelope]:
    """Read a bounded native timeline page for one exact thread.

    Args:
        payload: Exact target, cwd, thread ID, and pagination controls.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    params = {"threadId": thread_id, "limit": payload.limit}
    if payload.cursor is not None:
        params["cursor"] = payload.cursor

    def invoke(client):
        _read_guarded_thread(client, thread_id, cwd)
        return client.request("thread/timeline/list", params)

    return _run("list_thread_timeline", payload.target, invoke)


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


def get_thread_snapshot(payload: ThreadSnapshotRequest) -> Response[RpcEnvelope]:
    """Return a byte-bounded native status projection for an exact thread/worktree.

    Args:
        payload: Target, exact CWD/thread identity, and optional prior revision.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")

    def invoke(client):
        response = client.request(
            "thread/read", {"threadId": thread_id, "includeTurns": False}
        )
        thread = response.get("thread") if isinstance(response, dict) else None
        if (
            not isinstance(thread, dict)
            or thread.get("id") != thread_id
            or thread.get("cwd") != cwd
            or not isinstance(thread.get("turns"), list)
            or thread["turns"]
        ):
            raise RpcError("Native snapshot thread/CWD identity mismatch")

        turns_response = client.request(
            "thread/turns/list",
            {
                "threadId": thread_id,
                "limit": 1,
                "sortDirection": "desc",
                "itemsView": "notLoaded",
            },
        )
        turns = turns_response.get("data") if isinstance(turns_response, dict) else None
        if not isinstance(turns, list) or len(turns) > 1:
            raise RpcError("Native snapshot turn page is invalid")
        turn = turns[0] if turns else None
        if turn is not None and (
            not isinstance(turn, dict)
            or not isinstance(turn.get("id"), str)
            or not turn["id"]
            or len(turn["id"]) > 512
            or turn.get("items") != []
            or turn.get("itemsView") != "notLoaded"
        ):
            raise RpcError("Native snapshot turn identity is invalid")

        latest_item = None
        item_cursor = None
        if turn is not None:
            items_response = client.request(
                "thread/items/list",
                {
                    "threadId": thread_id,
                    "turnId": turn["id"],
                    "limit": 1,
                    "sortDirection": "desc",
                },
            )
            items = (
                items_response.get("data") if isinstance(items_response, dict) else None
            )
            item_cursor = (
                items_response.get("nextCursor")
                if isinstance(items_response, dict)
                else None
            )
            if (
                not isinstance(items, list)
                or len(items) > 1
                or (item_cursor is not None and not isinstance(item_cursor, str))
            ):
                raise RpcError("Native snapshot item page is invalid")
            latest_item = items[0] if items else None

        projected = _project_thread_snapshot(
            payload.target, cwd, thread_id, thread, turn, latest_item, item_cursor
        )
        observed_at = (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        snapshot = _snapshot_response(projected, observed_at)
        if payload.revision == snapshot["revision"]:
            snapshot = {
                "target": payload.target,
                "cwd": cwd,
                "thread_id": thread_id,
                "observed_at": observed_at,
                "revision": snapshot["revision"],
                "changed": False,
                "native_state_authoritative": True,
                "thread_status": snapshot["thread_status"],
                "active_flags": snapshot["active_flags"],
                "latest_turn": snapshot["latest_turn"],
            }
        return snapshot

    return _run(
        "get_thread_snapshot",
        payload.target,
        invoke,
        include_receipts=False,
        include_events=False,
        maximum_response_bytes=SNAPSHOT_MAX_BYTES,
    )


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
