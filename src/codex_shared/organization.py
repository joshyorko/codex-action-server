"""Shared organization behavior; importing this module registers no actions."""

from __future__ import annotations

from actions import ActionError
from typing import Any
from actions import Response
from codex_shared.models import RpcEnvelope
from codex_rpc import RpcError
from codex_shared.models import ThreadAttachmentAddRequest
from codex_shared.models import ThreadAttachmentListRequest
from codex_shared.models import ThreadAttachmentRemoveRequest
from codex_shared.models import ThreadBackgroundTerminalTerminateRequest
from codex_shared.models import ThreadBackgroundTerminalsListRequest
from codex_shared.models import ThreadForkRequest
from codex_shared.models import ThreadInjectItemsRequest
from codex_shared.models import ThreadMetadataUpdateRequest
from codex_shared.models import ThreadMutationRequest
from codex_shared.models import ThreadNameSetRequest
from codex_shared.models import ThreadRevertRequest
from codex_shared.models import ThreadSectionCreateRequest
from codex_shared.models import ThreadSectionDeleteRequest
from codex_shared.models import ThreadSectionMoveRequest
from codex_shared.models import ThreadSectionUpdateRequest
from codex_shared.common import _action_cwd
from codex_shared.common import _action_id
from codex_shared.common import _bounded_native_result
from codex_shared.common import _dispatch_run
from codex_shared.common import _read_guarded_thread
from codex_shared.common import _run


def _thread_control(
    operation: str,
    payload: ThreadMutationRequest,
    method: str,
    params: dict[str, Any],
    *,
    forked: bool = False,
) -> Response[RpcEnvelope]:
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)

    def invoke(client, receipt):
        _read_guarded_thread(client, thread_id, cwd)
        result = client.request(method, {"threadId": thread_id, **params})
        if forked:
            thread = result.get("thread") if isinstance(result, dict) else None
            if (
                not isinstance(thread, dict)
                or not isinstance(thread.get("id"), str)
                or not thread["id"]
                or thread.get("cwd") != cwd
            ):
                raise ActionError("Native fork response thread/cwd identity mismatch")
            receipt.update(state="accepted", thread_id=thread["id"])
        else:
            receipt.update(state="accepted", thread_id=thread_id)
        return result

    return _dispatch_run(operation, payload, invoke)


def fork_thread(payload: ThreadForkRequest) -> Response[RpcEnvelope]:
    """Fork one exact persisted thread without hydrating its transcript.

    Args:
        payload: Exact target, cwd, source thread, and bounded fork overrides.
    """
    params: dict[str, Any] = {"cwd": _action_cwd(payload.cwd), "excludeTurns": True}
    for key, value in (
        ("lastTurnId", payload.last_turn_id),
        ("model", payload.model),
        ("modelProvider", payload.model_provider),
    ):
        if value is not None:
            params[key] = value
    return _thread_control("fork_thread", payload, "thread/fork", params, forked=True)


def archive_thread(payload: ThreadMutationRequest) -> Response[RpcEnvelope]:
    """Archive the exact persisted thread.

    Args:
        payload: Exact target, cwd, thread ID, and required dispatch receipt key.
    """
    return _thread_control("archive_thread", payload, "thread/archive", {})


def unarchive_thread(payload: ThreadMutationRequest) -> Response[RpcEnvelope]:
    """Unarchive the exact persisted thread.

    Args:
        payload: Exact target, cwd, thread ID, and required dispatch receipt key.
    """
    return _thread_control("unarchive_thread", payload, "thread/unarchive", {})


def delete_thread(payload: ThreadMutationRequest) -> Response[RpcEnvelope]:
    """Delete the exact persisted thread.

    Args:
        payload: Exact target, cwd, thread ID, and required dispatch receipt key.
    """
    return _thread_control("delete_thread", payload, "thread/delete", {})


def set_thread_name(payload: ThreadNameSetRequest) -> Response[RpcEnvelope]:
    """Set the user-facing name of one exact persisted thread.

    Args:
        payload: Exact target, cwd, thread ID, name, and receipt key.
    """
    return _thread_control(
        "set_thread_name", payload, "thread/name/set", {"name": payload.name}
    )


def update_thread_metadata(
    payload: ThreadMetadataUpdateRequest,
) -> Response[RpcEnvelope]:
    """Patch Git metadata without changing omitted fields.

    Args:
        payload: Exact thread identity and at least one typed metadata field.
    """
    params: dict[str, Any] = {
        "gitInfo": {
            native: getattr(payload.git_info, field)
            for field, native in (
                ("sha", "sha"),
                ("branch", "branch"),
                ("origin_url", "originUrl"),
            )
            if field in payload.git_info.model_fields_set
        }
    }
    return _thread_control(
        "update_thread_metadata", payload, "thread/metadata/update", params
    )


def revert_thread(payload: ThreadRevertRequest) -> Response[RpcEnvelope]:
    """Revert persisted history to the prefix before one native turn ID.

    Args:
        payload: Exact thread identity and the native before-turn identifier.
    """
    return _thread_control(
        "revert_thread",
        payload,
        "thread/revert",
        {"beforeTurnId": _action_id(payload.before_turn_id, "before_turn_id")},
    )


def compact_thread(payload: ThreadMutationRequest) -> Response[RpcEnvelope]:
    """Request native compaction for the exact persisted thread.

    Args:
        payload: Exact target, cwd, thread ID, and required dispatch receipt key.
    """
    return _thread_control("compact_thread", payload, "thread/compact/start", {})


def list_background_terminals(
    payload: ThreadBackgroundTerminalsListRequest,
) -> Response[RpcEnvelope]:
    """List a bounded page of terminals attached to one exact thread/worktree.

    Args:
        payload: Target, exact thread/CWD identity, and pagination controls.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    params = {"threadId": thread_id, "limit": payload.limit}
    if payload.cursor is not None:
        params["cursor"] = payload.cursor

    def invoke(client):
        _read_guarded_thread(client, thread_id, cwd)
        result = client.request("thread/backgroundTerminals/list", params)
        page = result.get("data") if isinstance(result, dict) else None
        if not isinstance(page, list):
            raise RpcError("Native terminal list response is missing its data page")
        selected = []
        for terminal in page:
            if not isinstance(terminal, dict) or not isinstance(
                terminal.get("cwd"), str
            ):
                raise RpcError("Native terminal result is missing its CWD")
            if terminal["cwd"] == cwd:
                selected.append(terminal)
        return {
            "data": selected,
            "nextCursor": result.get("nextCursor"),
        }

    return _run(
        "list_background_terminals",
        payload.target,
        invoke,
        include_receipts=False,
    )


def terminate_background_terminal(
    payload: ThreadBackgroundTerminalTerminateRequest,
) -> Response[RpcEnvelope]:
    """Terminate a listed native terminal only after exact identity reconciliation.

    Args:
        payload: Exact thread/CWD, process ID, and required receipt key.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)

    def invoke(client, receipt):
        _read_guarded_thread(client, thread_id, cwd)
        page = client.request(
            "thread/backgroundTerminals/list",
            {"threadId": thread_id, "limit": 100},
        )
        terminals = page.get("data") if isinstance(page, dict) else None
        if not isinstance(terminals, list):
            raise RpcError("Native terminal list response is missing its data page")
        matches = [
            terminal
            for terminal in terminals
            if isinstance(terminal, dict)
            and terminal.get("processId") == payload.process_id
            and terminal.get("cwd") == cwd
            and isinstance(terminal.get("itemId"), str)
            and terminal["itemId"]
        ]
        if len(matches) != 1:
            raise RpcError(
                "Native terminal process identity is not unique in the bounded page"
            )
        result = client.request(
            "thread/backgroundTerminals/terminate",
            {"threadId": thread_id, "processId": payload.process_id},
        )
        if not isinstance(result, dict) or result.get("terminated") is not True:
            raise RpcError("Native terminal termination was not confirmed")
        receipt.update(state="accepted", thread_id=thread_id)
        return result

    return _dispatch_run("terminate_background_terminal", payload, invoke)


def list_thread_attachments(
    payload: ThreadAttachmentListRequest,
) -> Response[RpcEnvelope]:
    """List a bounded attachment page from one exact thread.

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
        return client.request("thread/attachment/list", params)

    return _run("list_thread_attachments", payload.target, invoke)


def add_thread_attachment(
    payload: ThreadAttachmentAddRequest,
) -> Response[RpcEnvelope]:
    """Create or locate one bounded, typed thread attachment.

    Args:
        payload: Exact thread identity, attachment type/key, bounded JSON payload,
            and required dispatch receipt key.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    params = {
        "threadId": thread_id,
        "attachmentType": payload.attachment_type,
        "identityKey": payload.identity_key,
        "payload": payload.payload,
    }

    def invoke(client, receipt):
        _read_guarded_thread(client, thread_id, cwd)
        result = client.request("thread/attachment/add", params)
        attachment = result.get("attachment") if isinstance(result, dict) else None
        if (
            not isinstance(attachment, dict)
            or attachment.get("attachmentType") != payload.attachment_type
            or attachment.get("identityKey") != payload.identity_key
            or not isinstance(attachment.get("id"), str)
            or not attachment["id"]
        ):
            raise RpcError("Native attachment response identity mismatch")
        receipt.update(state="accepted", thread_id=thread_id)
        return result

    return _dispatch_run("add_thread_attachment", payload, invoke)


def remove_thread_attachment(
    payload: ThreadAttachmentRemoveRequest,
) -> Response[RpcEnvelope]:
    """Remove one thread attachment by its exact stable identity key.

    Args:
        payload: Exact thread, attachment type/key, and receipt key.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    params = {
        "threadId": thread_id,
        "attachmentType": payload.attachment_type,
        "identityKey": payload.identity_key,
    }

    def invoke(client, receipt):
        _read_guarded_thread(client, thread_id, cwd)
        result = client.request("thread/attachment/remove", params)
        receipt.update(state="accepted", thread_id=thread_id)
        return result

    return _dispatch_run("remove_thread_attachment", payload, invoke)


def inject_thread_items(payload: ThreadInjectItemsRequest) -> Response[RpcEnvelope]:
    """Append a bounded list of typed user-text message items to one thread.

    Args:
        payload: Exact thread identity, restricted input-text items, and receipt key.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    params = {
        "threadId": thread_id,
        "items": [item.model_dump() for item in payload.items],
    }

    def invoke(client, receipt):
        _read_guarded_thread(client, thread_id, cwd)
        result = client.request("thread/inject_items", params)
        receipt.update(state="accepted", thread_id=thread_id)
        return result

    return _dispatch_run("inject_thread_items", payload, invoke)


def create_thread_section(
    payload: ThreadSectionCreateRequest,
) -> Response[RpcEnvelope]:
    """Create one named native thread section.

    Args:
        payload: Configured target, bounded section name, and receipt key.
    """

    def invoke(client, receipt):
        result = client.request("threadSection/create", {"name": payload.name})
        section = result.get("section") if isinstance(result, dict) else None
        if (
            not isinstance(section, dict)
            or not isinstance(section.get("id"), str)
            or not section["id"]
            or section.get("name") != payload.name
        ):
            raise RpcError("Native section creation response identity mismatch")
        receipt.update(state="accepted")
        return result

    return _dispatch_run("create_thread_section", payload, invoke)


def update_thread_section(
    payload: ThreadSectionUpdateRequest,
) -> Response[RpcEnvelope]:
    """Rename one exact native thread section.

    Args:
        payload: Configured target, stable section ID, name, and receipt key.
    """

    def invoke(client, receipt):
        result = client.request(
            "threadSection/update",
            {"sectionId": payload.section_id, "name": payload.name},
        )
        section = result.get("section") if isinstance(result, dict) else None
        if (
            not isinstance(section, dict)
            or section.get("id") != payload.section_id
            or section.get("name") != payload.name
        ):
            raise RpcError("Native section update response identity mismatch")
        receipt.update(state="accepted")
        return result

    return _dispatch_run("update_thread_section", payload, invoke)


def delete_thread_section(
    payload: ThreadSectionDeleteRequest,
) -> Response[RpcEnvelope]:
    """Delete one exact native thread section by its stable ID.

    Args:
        payload: Configured target, stable section ID, and receipt key.
    """

    def invoke(client, receipt):
        result = client.request(
            "threadSection/delete", {"sectionId": payload.section_id}
        )
        receipt.update(state="accepted")
        return result

    return _dispatch_run("delete_thread_section", payload, invoke)


def move_thread_to_section(
    payload: ThreadSectionMoveRequest,
) -> Response[RpcEnvelope]:
    """Move one exact thread into a section or remove it from its section.

    Args:
        payload: Exact thread/CWD, destination section, optional insertion anchor, and receipt ID.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    section_id = (
        _action_id(payload.section_id, "section_id")
        if payload.section_id is not None
        else None
    )
    before_thread_id = (
        _action_id(payload.before_thread_id, "before_thread_id")
        if payload.before_thread_id is not None
        else None
    )
    if before_thread_id == thread_id:
        raise ActionError("before_thread_id must differ from thread_id")
    params: dict[str, Any] = {"threadId": thread_id, "sectionId": section_id}
    if before_thread_id is not None:
        params["beforeThreadId"] = before_thread_id

    def invoke(client, receipt):
        _read_guarded_thread(client, thread_id, cwd)
        if before_thread_id is not None:
            _read_guarded_thread(client, before_thread_id, cwd)
        if section_id is not None:
            sections = client.request("threadSection/list", {"limit": 100})
            page = sections.get("data") if isinstance(sections, dict) else None
            if not isinstance(page, list):
                raise RpcError("Native section list is missing its data page")
            if not any(
                isinstance(section, dict) and section.get("id") == section_id
                for section in page
            ):
                if sections.get("nextCursor") is not None:
                    raise RpcError(
                        "Native destination section was not found in the bounded first page"
                    )
                raise ActionError("Destination section does not exist on this target")
        result = client.request("thread/section/move", params)
        if not isinstance(result, dict):
            raise RpcError("Native section move response is not an object")
        _bounded_native_result(result)
        receipt.update(state="accepted", thread_id=thread_id)
        return result

    return _dispatch_run("move_thread_to_section", payload, invoke)
