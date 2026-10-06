"""Native experimental queue mappings are version gated and identity scoped."""

import json
from unittest.mock import Mock, patch

import pytest

import codex_rpc
from test_actions import FakeClient, load_actions
from native_wire_contracts import (
    assert_native_request_contract,
    assert_native_response_contract,
)


class QueueClient(FakeClient):
    def request(self, method, params):
        self.calls.append((method, params))
        if method == "thread/read":
            return {"thread": {"id": params["threadId"], "cwd": "/trusted"}}
        if method == "thread/queue/list":
            return {"data": [], "nextCursor": None}
        if method == "thread/queue/add":
            return {
                "queuedSubmission": {
                    "id": "queue-1",
                    "input": [{"type": "text", "text": "run this later"}],
                    "clientUserMessageId": "message-1",
                }
            }
        if method == "thread/queue/update":
            return {
                "queuedSubmission": {
                    "id": params["queuedSubmissionId"],
                    "input": [{"type": "text", "text": "updated text"}],
                    "clientUserMessageId": "message-1",
                }
            }
        if method == "thread/queue/delete":
            return {"deleted": True}
        if method in {"thread/queue/reorder"}:
            return {}
        if method == "thread/queue/start":
            return {"turn": {"id": "turn-queue", "status": "inProgress"}}
        raise AssertionError(method)


def test_queue_actions_map_native_fields_and_persist_acknowledged_receipts(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_ACTION_RECEIPTS", str(tmp_path))
    module = load_actions()
    client = QueueClient(None)
    requests = [
        (
            module.list_thread_queue,
            module.ThreadQueueListRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                limit=10,
                cursor="queue-next",
            ),
        ),
        (
            module.add_thread_queue_item,
            module.ThreadQueueAddRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                request_id="queue-add",
                client_user_message_id="message-1",
                text="run this later",
            ),
        ),
        (
            module.update_thread_queue_item,
            module.ThreadQueueUpdateRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                request_id="queue-update",
                queued_submission_id="queue-1",
                text="updated text",
            ),
        ),
        (
            module.delete_thread_queue_item,
            module.ThreadQueueDeleteRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                request_id="queue-delete",
                queued_submission_id="queue-1",
            ),
        ),
        (
            module.reorder_thread_queue,
            module.ThreadQueueReorderRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                request_id="queue-reorder",
                queued_submission_ids=["queue-1", "queue-2"],
            ),
        ),
        (
            module.start_thread_queue,
            module.ThreadQueueStartRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                request_id="queue-start",
                queued_submission_id="queue-1",
            ),
        ),
    ]

    with patch.object(module, "Client", return_value=client):
        results = [function(request).result.result for function, request in requests]

    assert results[0]["data"] == []
    assert all(result["dispatch"]["state"] == "accepted" for result in results[1:])
    assert results[-1]["dispatch"]["turn_id"] == "turn-queue"
    assert [method for method, _params in client.calls if method != "thread/read"] == [
        "thread/queue/list",
        "thread/queue/add",
        "thread/queue/update",
        "thread/queue/delete",
        "thread/queue/reorder",
        "thread/queue/start",
    ]
    assert client.calls[1][1] == {
        "threadId": "thread-1",
        "limit": 10,
        "cursor": "queue-next",
    }
    assert client.calls[3][1] == {
        "threadId": "thread-1",
        "input": [{"type": "text", "text": "run this later"}],
        "clientUserMessageId": "message-1",
    }
    assert client.calls[5][1] == {
        "threadId": "thread-1",
        "queuedSubmissionId": "queue-1",
        "input": [{"type": "text", "text": "updated text"}],
    }
    assert client.calls[9][1] == {
        "threadId": "thread-1",
        "queuedSubmissionIds": ["queue-1", "queue-2"],
    }
    assert client.calls[11][1] == {
        "threadId": "thread-1",
        "queuedSubmissionId": "queue-1",
    }
    for method, params in client.calls:
        if method in {
            "thread/queue/add",
            "thread/queue/list",
            "thread/queue/update",
            "thread/queue/delete",
            "thread/queue/reorder",
            "thread/queue/start",
        }:
            assert_native_request_contract(method, params)
    assert_native_response_contract("thread/queue/list", results[0])
    assert_native_response_contract(
        "thread/queue/add", {"queuedSubmission": results[1]["queuedSubmission"]}
    )
    assert_native_response_contract(
        "thread/queue/update", {"queuedSubmission": results[2]["queuedSubmission"]}
    )
    assert_native_response_contract(
        "thread/queue/delete", {"deleted": results[3]["deleted"]}
    )
    assert_native_response_contract("thread/queue/reorder", {})
    assert_native_response_contract(
        "thread/queue/start", {"turn": {"id": "turn-queue"}}
    )


def test_queue_mutations_reject_cwd_mismatch_before_native_queue_method(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_ACTION_RECEIPTS", str(tmp_path))
    module = load_actions()
    client = QueueClient(None)

    def mismatch(method, params):
        client.calls.append((method, params))
        return (
            {"thread": {"id": params["threadId"], "cwd": "/other"}}
            if method == "thread/read"
            else {}
        )

    client.request = mismatch
    request = module.ThreadQueueDeleteRequest(
        target="local",
        cwd="/trusted",
        thread_id="thread-1",
        request_id="queue-delete",
        queued_submission_id="queue-1",
    )
    with patch.object(module, "Client", return_value=client):
        result = module.delete_thread_queue_item(request).result.result

    assert [method for method, _params in client.calls] == ["thread/read"]
    assert result["dispatch"]["state"] == "unknown"


@pytest.mark.parametrize("method", sorted(codex_rpc.EXPERIMENTAL_METHODS))
def test_experimental_methods_reject_unpinned_daemon_before_dispatch(method):
    client = codex_rpc.Client(codex_rpc.Target("local", socket_path="fixture"))
    client.ws = Mock()
    client.metadata = {"userAgent": "codex-cli 0.153.4"}

    with pytest.raises(codex_rpc.RpcError, match="pinned experimental"):
        client.request(method, {})
    client.ws.send.assert_not_called()


def test_experimental_queue_method_uses_pinned_schema_version():
    client = codex_rpc.Client(codex_rpc.Target("local", socket_path="fixture"))
    client.ws = Mock()
    client.metadata = {"userAgent": "codex-cli 0.160.1"}
    client.ws.recv.return_value = json.dumps(
        {"id": 1, "result": {"queuedSubmission": {"id": "queue-1"}}}
    )

    result = client.request(
        "thread/queue/add",
        {
            "threadId": "thread-1",
            "input": [{"type": "text", "text": "queued"}],
            "clientUserMessageId": "message-1",
        },
    )

    assert result == {"queuedSubmission": {"id": "queue-1"}}
