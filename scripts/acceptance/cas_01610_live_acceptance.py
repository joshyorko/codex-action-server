#!/usr/bin/env python3
"""Run scoped CAS actions against the already-running local native daemon."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import time
import uuid

import codex_actions as actions
from codex_rpc import Client
from native_capabilities import SCHEMA_VERSION
from codex_rpc import native_server_build_version
from codex_shared.models import CreateThreadAndStartTurnRequest
from codex_shared.models import ServerDiagnosticsRequest
from codex_shared.models import ThreadMutationRequest
from codex_shared.models import ThreadNameSetRequest
from codex_shared.models import ThreadQueueAddRequest
from codex_shared.models import ThreadQueueDeleteRequest
from codex_shared.models import ThreadQueueListRequest
from codex_shared.models import ThreadQueueReorderRequest
from codex_shared.models import ThreadQueueStartRequest
from codex_shared.models import ThreadQueueUpdateRequest
from codex_shared.models import ThreadReadRequest
from codex_shared.models import TurnStartRequest
from codex_shared.models import ThreadSnapshotRequest
from codex_shared.models import ThreadTurnsListRequest
from codex_shared.models import TurnInterruptRequest


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_CWD = "/home/kdlocpanda/.codex/worktrees/cas-production-readiness"
AUDITED_METHODS = {
    "server/diagnostics",
    "thread/queue/add",
    "thread/queue/delete",
    "thread/queue/list",
    "thread/queue/reorder",
    "thread/queue/start",
    "thread/queue/update",
}


def response_envelope(response):
    return response.result


def record(result: dict, name: str, response, method: str | None = None) -> dict:
    envelope = response_envelope(response)
    value = {
        "operation": envelope.operation,
        "native_methods": [
            receipt.get("request", {}).get("method")
            for receipt in envelope.receipts
            if receipt.get("request", {}).get("method")
        ],
    }
    value["dispatch"] = envelope.result.get("dispatch")
    value["result_keys"] = sorted(key for key in envelope.result if key != "dispatch")
    result[name] = value
    return envelope.result


def main() -> None:
    config_path = Path(os.environ["CODEX_ACTION_TARGETS"])
    config = json.loads(config_path.read_text())
    targets = config.get("targets", {})
    if set(targets) != {"local"}:
        raise SystemExit("acceptance requires one exact local target")
    local = targets["local"]
    if set(local) != {"transport", "codex_bin", "socket_path"}:
        raise SystemExit("acceptance requires a socket-only local target")
    if local["transport"] != "local":
        raise SystemExit("acceptance target must use local Unix-socket transport")
    socket_path = Path(local["socket_path"])
    if not socket_path.is_absolute() or not stat.S_ISSOCK(socket_path.stat().st_mode):
        raise SystemExit("acceptance requires the existing native Unix socket")
    receipt_path = Path(os.environ["CODEX_ACTION_RECEIPTS"])
    if not receipt_path.is_absolute() or not str(receipt_path).startswith(
        "/tmp/cas-01610-acceptance/"
    ):
        raise SystemExit("acceptance receipts must use the isolated /tmp directory")
    if str(ROOT) != EXPECTED_CWD:
        raise SystemExit("acceptance must run from the dedicated readiness worktree")

    original_request = Client.request
    request_audit = []

    def audited_request(client, method, params, timeout=None):
        previous_id = client.next_id
        receipt_count = len(client.receipts)
        try:
            return original_request(client, method, params, timeout)
        finally:
            if method in AUDITED_METHODS:
                received = client.receipts[receipt_count:]
                method_response = any(
                    entry.get("request", {}).get("method") == method
                    for entry in received
                )
                rpc_error = next(
                    (
                        entry.get("response", {}).get("error", {}).get("code")
                        for entry in received
                        if entry.get("request", {}).get("method") == method
                        and isinstance(entry.get("response", {}).get("error"), dict)
                    ),
                    None,
                )
                request_audit.append(
                    {
                        "method": method,
                        "request_id_advanced": client.next_id == previous_id + 1,
                        "native_response_received": method_response,
                        "native_rpc_error_code": rpc_error,
                    }
                )

    Client.request = audited_request
    run_id = "cas01610-" + uuid.uuid4().hex[:12]
    target = "local"
    cwd = EXPECTED_CWD
    result: dict = {"cas_schema_version": SCHEMA_VERSION}
    thread_id = None
    queued_submission_ids = []
    active_turn_id = None
    try:
        try:
            diagnostics = actions.read_server_diagnostics(
                ServerDiagnosticsRequest(target=target)
            )
            envelope = response_envelope(diagnostics)
            result["diagnostics"] = {
                "server_version": native_server_build_version(
                    envelope.connection.server
                ),
                "native_methods": [
                    receipt.get("request", {}).get("method")
                    for receipt in envelope.receipts
                    if receipt.get("request", {}).get("method")
                ],
                "process_id_present": bool(
                    envelope.result.get("process", {}).get("id")
                ),
                "gauge_count": len(envelope.result.get("gauges", [])),
                "dispatch_error_code": envelope.result.get("dispatch", {}).get(
                    "error_code"
                ),
            }
        except Exception as error:  # report type only; native error data may be private
            result["diagnostics"] = {"error_type": type(error).__name__}

        created = actions.create_thread_and_start_turn(
            CreateThreadAndStartTurnRequest(
                request_id=f"{run_id}-create",
                target=target,
                cwd=cwd,
                model="gpt-6-luna",
                effort="low",
                text=f"Disposable CAS 0.161.0 acceptance {run_id}. Reply exactly CAS_FIRST_TURN_OK. Do not use tools or modify files.",
                wait_for_completion=True,
                wait_seconds=30,
            )
        )
        created_result = record(result, "create_and_first_turn", created)
        thread_id = created_result.get("created", {}).get("thread", {}).get("id")
        if not isinstance(thread_id, str) or not thread_id:
            raise RuntimeError("thread creation returned no exact thread ID")
        result["thread_id"] = thread_id
        actions.set_thread_name(
            ThreadNameSetRequest(
                request_id=f"{run_id}-name",
                target=target,
                cwd=cwd,
                thread_id=thread_id,
                name=f"CAS 0.161 disposable {run_id}",
            )
        )

        active_turn = actions.start_turn(
            TurnStartRequest(
                request_id=f"{run_id}-active-turn",
                target=target,
                cwd=cwd,
                thread_id=thread_id,
                text=f"For disposable acceptance {run_id}, write a detailed 1,500-word release checklist. Do not use tools or modify files. Continue until interrupted.",
                model="gpt-6-luna",
                effort="low",
                wait_for_completion=False,
            )
        )
        active_turn_result = record(result, "active_turn_start", active_turn)
        active_turn_id = active_turn_result.get("dispatch", {}).get(
            "turn_id"
        ) or active_turn_result.get("accepted", {}).get("turn", {}).get("id")
        if not isinstance(active_turn_id, str) or not active_turn_id:
            raise RuntimeError("active turn returned no exact turn ID")

        queue_scope = {"target": target, "cwd": cwd, "thread_id": thread_id}
        initial = actions.list_thread_queue(ThreadQueueListRequest(**queue_scope))
        record(result, "queue_list_initial", initial)

        added = actions.add_thread_queue_item(
            ThreadQueueAddRequest(
                **queue_scope,
                request_id=f"{run_id}-queue-add",
                client_user_message_id=f"{run_id}-queue-message",
                text=f"Temporary queue fixture {run_id}.",
            )
        )
        added_result = record(result, "queue_add", added)
        first_submission_id = added_result.get("queuedSubmission", {}).get("id")
        if not isinstance(first_submission_id, str) or not first_submission_id:
            raise RuntimeError("queue add returned no exact submission ID")
        queued_submission_ids.append(first_submission_id)

        second_added = actions.add_thread_queue_item(
            ThreadQueueAddRequest(
                **queue_scope,
                request_id=f"{run_id}-queue-add-second",
                client_user_message_id=f"{run_id}-queue-message-second",
                text=f"Second temporary queue fixture {run_id}.",
            )
        )
        second_result = record(result, "queue_add_second", second_added)
        second_submission_id = second_result.get("queuedSubmission", {}).get("id")
        if not isinstance(second_submission_id, str) or not second_submission_id:
            raise RuntimeError("second queue add returned no exact submission ID")
        queued_submission_ids.append(second_submission_id)

        listed_before_update = actions.list_thread_queue(
            ThreadQueueListRequest(**queue_scope)
        )
        listed_before_result = record(
            result, "queue_list_before_update", listed_before_update
        )
        before_items = listed_before_result.get("data", [])
        result["queue_list_before_update"]["item_count"] = len(before_items)
        result["queue_list_before_update"]["ids_match"] = {
            item.get("id") for item in before_items
        } == set(queued_submission_ids)

        updated = actions.update_thread_queue_item(
            ThreadQueueUpdateRequest(
                **queue_scope,
                request_id=f"{run_id}-queue-update",
                queued_submission_id=first_submission_id,
                text=f"Updated temporary queue fixture {run_id}.",
            )
        )
        record(result, "queue_update", updated)
        listed = actions.list_thread_queue(ThreadQueueListRequest(**queue_scope))
        listed_result = record(result, "queue_list_updated", listed)
        queue_items = listed_result.get("data", [])
        result["queue_list_updated"]["item_count"] = len(queue_items)
        updated_item = next(
            (item for item in queue_items if item.get("id") == first_submission_id),
            None,
        )
        result["queue_list_updated"]["updated_item_present"] = updated_item is not None
        result["queue_list_updated"]["updated_text_matches"] = bool(
            updated_item
            and f"Updated temporary queue fixture {run_id}."
            in json.dumps(updated_item.get("input"), sort_keys=True)
        )

        reordered = actions.reorder_thread_queue(
            ThreadQueueReorderRequest(
                **queue_scope,
                request_id=f"{run_id}-queue-reorder",
                queued_submission_ids=[second_submission_id, first_submission_id],
            )
        )
        record(result, "queue_reorder", reordered)

        listed_after_reorder = actions.list_thread_queue(
            ThreadQueueListRequest(**queue_scope)
        )
        reordered_result = record(result, "queue_list_reordered", listed_after_reorder)
        result["queue_list_reordered"]["order_matches"] = [
            item.get("id") for item in reordered_result.get("data", [])
        ] == [second_submission_id, first_submission_id]

        queue_start = actions.start_thread_queue(
            ThreadQueueStartRequest(
                **queue_scope,
                request_id=f"{run_id}-queue-start-active",
                queued_submission_id=second_submission_id,
            )
        )
        queue_start_result = record(result, "queue_start_while_active", queue_start)
        result["queue_start_while_active"]["state_rejection"] = queue_start_result.get(
            "dispatch", {}
        ).get("error_code")

        deleted = actions.delete_thread_queue_item(
            ThreadQueueDeleteRequest(
                **queue_scope,
                request_id=f"{run_id}-queue-delete",
                queued_submission_id=first_submission_id,
            )
        )
        record(result, "queue_delete", deleted)
        if result["queue_delete"].get("dispatch", {}).get("state") == "accepted":
            queued_submission_ids.remove(first_submission_id)
        remaining_delete = actions.delete_thread_queue_item(
            ThreadQueueDeleteRequest(
                **queue_scope,
                request_id=f"{run_id}-queue-delete-second",
                queued_submission_id=second_submission_id,
            )
        )
        record(result, "queue_delete_second", remaining_delete)
        if result["queue_delete_second"].get("dispatch", {}).get("state") == "accepted":
            queued_submission_ids.remove(second_submission_id)

        interrupted = actions.interrupt_turn(
            TurnInterruptRequest(
                target=target,
                cwd=cwd,
                thread_id=thread_id,
                turn_id=active_turn_id,
            )
        )
        record(result, "active_turn_interrupt", interrupted)
        time.sleep(0.5)
        terminal_turns = actions.list_thread_turns(
            ThreadTurnsListRequest(
                **queue_scope,
                limit=10,
                sort_direction="asc",
                items_view="summary",
            )
        )
        terminal_rows = response_envelope(terminal_turns).result.get("data", [])
        terminal_turn = next(
            (turn for turn in terminal_rows if turn.get("id") == active_turn_id),
            None,
        )
        result["active_turn_terminal_status"] = (
            terminal_turn.get("status") if terminal_turn else "not_observed"
        )
        if result["active_turn_terminal_status"] == "interrupted":
            active_turn_id = None

        queue_start_item = actions.add_thread_queue_item(
            ThreadQueueAddRequest(
                **queue_scope,
                request_id=f"{run_id}-queue-start-add",
                client_user_message_id=f"{run_id}-start-message",
                text=f"Reply exactly CAS_QUEUE_START_OK for disposable run {run_id}. Do not use tools or modify files.",
            )
        )
        start_item_result = record(
            result, "queue_start_add_after_interrupt", queue_start_item
        )
        start_submission_id = start_item_result.get("queuedSubmission", {}).get("id")
        if not isinstance(start_submission_id, str) or not start_submission_id:
            raise RuntimeError("queue start fixture returned no exact submission ID")
        queued_submission_ids.append(start_submission_id)
        pending_for_start = actions.list_thread_queue(
            ThreadQueueListRequest(**queue_scope)
        )
        pending_start_result = record(
            result, "queue_list_before_start", pending_for_start
        )
        result["queue_list_before_start"]["item_present"] = any(
            item.get("id") == start_submission_id
            for item in pending_start_result.get("data", [])
        )

        queue_started = actions.start_thread_queue(
            ThreadQueueStartRequest(
                **queue_scope,
                request_id=f"{run_id}-queue-start-idle",
                queued_submission_id=start_submission_id,
            )
        )
        queue_started_result = record(
            result, "queue_start_after_interrupt", queue_started
        )
        queue_start_dispatch = queue_started_result.get("dispatch", {})
        queue_started_turn_id = queue_start_dispatch.get("turn_id")
        if queue_start_dispatch.get("state") == "accepted":
            queued_submission_ids.remove(start_submission_id)
        if not isinstance(queue_started_turn_id, str) or not queue_started_turn_id:
            raise RuntimeError("queue start returned no exact turn ID")

        active_turn_id = queue_started_turn_id
        for _ in range(24):
            turns = actions.list_thread_turns(
                ThreadTurnsListRequest(
                    **queue_scope,
                    limit=10,
                    sort_direction="asc",
                    items_view="summary",
                )
            )
            rows = response_envelope(turns).result.get("data", [])
            queue_started_turn = next(
                (turn for turn in rows if turn.get("id") == queue_started_turn_id),
                None,
            )
            if queue_started_turn and queue_started_turn.get("status") != "inProgress":
                break
            time.sleep(0.5)
        result["queue_started_turn_status"] = (
            queue_started_turn.get("status") if queue_started_turn else "not_observed"
        )
        if queue_started_turn and queue_started_turn.get("status") == "inProgress":
            actions.interrupt_turn(
                TurnInterruptRequest(
                    target=target,
                    cwd=cwd,
                    thread_id=thread_id,
                    turn_id=queue_started_turn_id,
                )
            )
            result["queue_started_turn_cleanup"] = "interrupted_exact_turn"
        else:
            active_turn_id = None

        final_queue = actions.list_thread_queue(ThreadQueueListRequest(**queue_scope))
        final_queue_result = record(result, "queue_list_final", final_queue)
        result["queue_list_final"]["item_count"] = len(
            final_queue_result.get("data", [])
        )

        snapshot = actions.get_thread_snapshot(
            ThreadSnapshotRequest(
                target=target,
                cwd=cwd,
                thread_id=thread_id,
            )
        )
        result["snapshot_status"] = (
            response_envelope(snapshot).result.get("thread", {}).get("status")
        )
    except Exception as error:
        result["harness_error_type"] = type(error).__name__
    finally:
        try:
            if thread_id and queued_submission_ids:
                queue_state = actions.list_thread_queue(
                    ThreadQueueListRequest(
                        target=target,
                        cwd=cwd,
                        thread_id=thread_id,
                    )
                )
                current_ids = {
                    item.get("id")
                    for item in response_envelope(queue_state).result.get("data", [])
                }
                for index, submission_id in enumerate(tuple(queued_submission_ids)):
                    if submission_id not in current_ids:
                        queued_submission_ids.remove(submission_id)
                        continue
                    cleanup = actions.delete_thread_queue_item(
                        ThreadQueueDeleteRequest(
                            target=target,
                            cwd=cwd,
                            thread_id=thread_id,
                            request_id=f"{run_id}-queue-cleanup-{index}",
                            queued_submission_id=submission_id,
                        )
                    )
                    cleanup_dispatch = response_envelope(cleanup).result.get(
                        "dispatch", {}
                    )
                    if cleanup_dispatch.get("state") == "accepted":
                        queued_submission_ids.remove(submission_id)
                result["pending_queue_items_after_cleanup"] = list(
                    queued_submission_ids
                )
        except Exception as error:
            result["pending_queue_item_cleanup_error"] = type(error).__name__
        finally:
            try:
                if thread_id:
                    if active_turn_id:
                        turns = actions.list_thread_turns(
                            ThreadTurnsListRequest(
                                target=target,
                                cwd=cwd,
                                thread_id=thread_id,
                                limit=10,
                                sort_direction="asc",
                                items_view="summary",
                            )
                        )
                        active_turn = next(
                            (
                                turn
                                for turn in response_envelope(turns).result.get(
                                    "data", []
                                )
                                if turn.get("id") == active_turn_id
                            ),
                            None,
                        )
                        if active_turn and active_turn.get("status") == "inProgress":
                            actions.interrupt_turn(
                                TurnInterruptRequest(
                                    target=target,
                                    cwd=cwd,
                                    thread_id=thread_id,
                                    turn_id=active_turn_id,
                                )
                            )
                    deleted = actions.delete_thread(
                        ThreadMutationRequest(
                            request_id=f"{run_id}-thread-delete",
                            target=target,
                            cwd=cwd,
                            thread_id=thread_id,
                        )
                    )
                    result["thread_cleanup_dispatch"] = response_envelope(
                        deleted
                    ).result.get("dispatch", {})
                    try:
                        actions.read_thread(
                            ThreadReadRequest(
                                target=target,
                                cwd=cwd,
                                thread_id=thread_id,
                                include_turns=False,
                            )
                        )
                    except Exception as error:
                        result["deleted_thread_readback_error_type"] = type(
                            error
                        ).__name__
                    else:
                        result["deleted_thread_readback_error_type"] = None
            except Exception as error:
                result["thread_cleanup_error_type"] = type(error).__name__
            finally:
                Client.request = original_request
                result["native_request_audit"] = request_audit
                print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
