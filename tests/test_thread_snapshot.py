"""Bounded native supervisory snapshots preserve identity and status provenance."""

import json
from unittest.mock import patch

import pytest
from native_test_helpers import native_server_user_agent
from test_actions import load_actions
from native_wire_contracts import assert_native_request_contract

import codex_rpc


class SnapshotClient:
    def __init__(
        self,
        _target=None,
        *,
        status=None,
        cwd="/work",
        text="latest ",
        turn_status="failed",
        item_status=None,
        item_phase="final",
        item_cursor="next-item",
    ):
        self.calls = []
        self.events = [{"method": "large-native-event", "payload": "x" * 100_000}]
        self.receipts = [{"result": "x" * 100_000}]
        self.metadata = {"userAgent": native_server_user_agent("0.160.1")}
        self.status = status or {
            "type": "active",
            "activeFlags": ["waitingOnApproval", "waitingOnUserInput"],
        }
        self.cwd = cwd
        self.text = text
        self.turn_status = turn_status
        self.item_status = item_status
        self.item_phase = item_phase
        self.item_cursor = item_cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def provenance(self):
        return {"target": "local", "codexBin": "codex", "server": "fixture"}

    def request(self, method, params):
        assert_native_request_contract(method, params)
        self.calls.append((method, params))
        if method == "thread/read":
            return {
                "thread": {
                    "id": params["threadId"],
                    "cwd": self.cwd,
                    "status": self.status,
                    "turns": [],
                    "updatedAt": 1760000000,
                }
            }
        if method == "thread/turns/list":
            return {
                "data": [
                    {
                        "id": "turn-1",
                        "status": self.turn_status,
                        "items": [],
                        "itemsView": "notLoaded",
                        "error": {
                            "message": "detail intentionally omitted",
                            "codexErrorInfo": "rateLimitExceeded",
                        },
                    }
                ]
            }
        if method == "thread/items/list":
            return {
                "data": [
                    {
                        "turnId": "turn-1",
                        "item": {
                            "id": "item-1",
                            "type": "agentMessage",
                            "phase": self.item_phase,
                            "status": self.item_status,
                            "text": self.text,
                        },
                    }
                ],
                "nextCursor": self.item_cursor,
            }
        raise AssertionError(method)


def run_snapshot(client, payload=None):
    module = load_actions()
    request = payload or module.ThreadSnapshotRequest(
        target="local", cwd="/work", thread_id="thread-1"
    )
    with (
        patch.object(module, "Client", return_value=client),
        patch.object(module, "resolve_target", return_value="local"),
    ):
        response = module.get_thread_snapshot(request)
    return response, client


def test_snapshot_is_identity_scoped_compact_and_does_not_return_receipts_or_events():
    response, client = run_snapshot(SnapshotClient(text="🙂" * 10_000))
    envelope = response.result
    snapshot = envelope.result
    encoded = json.dumps(
        response.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")

    assert len(encoded) <= 8192
    assert snapshot["source"] == "native-app-server"
    assert snapshot["target"] == "local"
    assert snapshot["cwd"] == "/work"
    assert snapshot["thread_id"] == "thread-1"
    assert snapshot["thread_status"] == "active"
    assert snapshot["active_flags"] == ["waitingOnApproval", "waitingOnUserInput"]
    assert snapshot["native_updated_at"] == 1760000000
    assert snapshot["native_updated_at_unknown"] is False
    assert snapshot["latest_turn"] == {
        "id": "turn-1",
        "status": "failed",
        "error_code": "rateLimitExceeded",
    }
    assert snapshot["latest_item"]["phase"] == "final"
    assert snapshot["latest_item"]["text_truncated"] is True
    assert len(snapshot["latest_item"]["text"].encode("utf-8")) <= 512
    assert snapshot["continuation"] == {
        "turn_id": "turn-1",
        "item_cursor": "next-item",
        "item_cursor_omitted": False,
        "item_cursor_digest": None,
    }
    assert envelope.receipts == []
    assert envelope.events == []
    assert [method for method, _ in client.calls] == [
        "thread/read",
        "thread/turns/list",
        "thread/items/list",
    ]
    assert client.calls[0][1] == {
        "threadId": "thread-1",
        "includeTurns": False,
    }
    assert client.calls[1][1] == {
        "threadId": "thread-1",
        "limit": 1,
        "sortDirection": "desc",
        "itemsView": "notLoaded",
    }
    assert client.calls[2][1] == {
        "threadId": "thread-1",
        "turnId": "turn-1",
        "limit": 1,
        "sortDirection": "desc",
    }


def test_snapshot_utf8_truncation_never_splits_a_codepoint():
    module = load_actions()
    text, truncated = module._utf8_truncate("a" * 508 + "🙂" + "z", 512)

    assert truncated
    assert len(text.encode("utf-8")) <= 512
    assert text.endswith("…")
    text.encode("utf-8").decode("utf-8")
    exact, was_truncated = module._utf8_truncate("🙂" * 128, 512)
    assert exact == "🙂" * 128
    assert not was_truncated


def test_snapshot_omits_oversized_native_cursor_but_hashes_its_revision():
    module = load_actions()
    projection = module._project_thread_snapshot(
        "local",
        "/work",
        "thread-1",
        {
            "status": {"type": "idle"},
            "updatedAt": 1760000000,
        },
        {"id": "turn-1", "status": "completed", "error": None},
        None,
        "cursor-" + "x" * 1024,
    )

    assert projection["continuation"]["item_cursor"] is None
    assert projection["continuation"]["item_cursor_omitted"] is True
    assert len(projection["continuation"]["item_cursor_digest"]) == 64


def test_snapshot_same_revision_is_small_but_retains_native_status_and_errors():
    first_response, _ = run_snapshot(SnapshotClient())
    first = first_response.result.result
    payload = load_actions().ThreadSnapshotRequest(
        target="local",
        cwd="/work",
        thread_id="thread-1",
        revision=first["revision"],
    )
    unchanged_response, _ = run_snapshot(SnapshotClient(), payload)
    unchanged = unchanged_response.result.result

    assert unchanged["changed"] is False
    assert unchanged["revision"] == first["revision"]
    assert unchanged["thread_status"] == "active"
    assert unchanged["active_flags"] == first["active_flags"]
    assert unchanged["latest_turn"] == first["latest_turn"]
    assert "latest_item" not in unchanged
    assert len(json.dumps(unchanged).encode()) < len(json.dumps(first).encode())


def test_snapshot_status_change_invalidates_revision():
    first, _ = run_snapshot(SnapshotClient())
    changed, _ = run_snapshot(
        SnapshotClient(status={"type": "idle", "activeFlags": []}),
        load_actions().ThreadSnapshotRequest(
            target="local",
            cwd="/work",
            thread_id="thread-1",
            revision=first.result.result["revision"],
        ),
    )

    assert changed.result.result["changed"] is True
    assert changed.result.result["thread_status"] == "idle"
    assert changed.result.result["revision"] != first.result.result["revision"]


def test_snapshot_rejects_mismatched_cwd_before_turn_or_item_reads():
    client = SnapshotClient(cwd="/other")
    module = load_actions()
    with (
        patch.object(module, "Client", return_value=client),
        patch.object(module, "resolve_target", return_value="local"),
        pytest.raises(module.ActionError, match="thread/CWD identity mismatch"),
    ):
        module.get_thread_snapshot(
            module.ThreadSnapshotRequest(
                target="local", cwd="/work", thread_id="thread-1"
            )
        )

    assert [method for method, _ in client.calls] == ["thread/read"]


def test_snapshot_unavailability_never_becomes_idle():
    class Unavailable(SnapshotClient):
        def request(self, method, params):
            raise codex_rpc.RpcError("offline")

    module = load_actions()
    with (
        patch.object(module, "Client", return_value=Unavailable()),
        patch.object(module, "resolve_target", return_value="local"),
        pytest.raises(module.ActionError, match="offline"),
    ):
        module.get_thread_snapshot(
            module.ThreadSnapshotRequest(
                target="local", cwd="/work", thread_id="thread-1"
            )
        )


def test_snapshot_projects_future_native_turn_status_as_unknown():
    response, _ = run_snapshot(SnapshotClient(turn_status="waitingForExternalTask"))

    assert response.result.result["latest_turn"] == {
        "id": "turn-1",
        "status": "unknown",
        "native_status_unknown": True,
        "error_code": "rateLimitExceeded",
    }


@pytest.mark.parametrize(
    ("first_client", "second_client", "raw_values"),
    [
        (
            SnapshotClient(
                status={"type": "futureThreadA", "activeFlags": []},
                turn_status="completed",
            ),
            SnapshotClient(
                status={"type": "futureThreadB", "activeFlags": []},
                turn_status="completed",
            ),
            ("futureThreadA", "futureThreadB"),
        ),
        (
            SnapshotClient(turn_status="futureTurnA"),
            SnapshotClient(turn_status="futureTurnB"),
            ("futureTurnA", "futureTurnB"),
        ),
        (
            SnapshotClient(status={"type": "active", "activeFlags": ["futureFlagA"]}),
            SnapshotClient(status={"type": "active", "activeFlags": ["futureFlagB"]}),
            ("futureFlagA", "futureFlagB"),
        ),
    ],
)
def test_unknown_native_state_change_invalidates_revision_without_disclosure(
    first_client, second_client, raw_values
):
    first, _ = run_snapshot(first_client)
    second, _ = run_snapshot(
        second_client,
        load_actions().ThreadSnapshotRequest(
            target="local",
            cwd="/work",
            thread_id="thread-1",
            revision=first.result.result["revision"],
        ),
    )
    encoded = json.dumps(second.model_dump(mode="json"), ensure_ascii=True)

    assert second.result.result["changed"] is True
    assert second.result.result["revision"] != first.result.result["revision"]
    assert all(raw_value not in encoded for raw_value in raw_values)


def test_snapshot_preserves_interrupted_turn_tail_without_reporting_completion():
    response, _ = run_snapshot(
        SnapshotClient(
            turn_status="interrupted",
            item_status="inProgress",
            item_phase="commentary",
            text="partial output",
        )
    )
    snapshot = response.result.result

    assert snapshot["latest_turn"]["status"] == "interrupted"
    assert snapshot["latest_item"]["status"] == "inProgress"
    assert snapshot["latest_item"]["phase"] == "commentary"
    assert snapshot["latest_item"]["text"] == "partial output"


def test_snapshot_after_wrapper_restart_uses_native_completion_not_expired_cursor():
    first, _ = run_snapshot(
        SnapshotClient(turn_status="inProgress", item_cursor="cursor-from-old-wrapper")
    )
    second_client = SnapshotClient(
        status={"type": "idle", "activeFlags": []},
        turn_status="completed",
        item_status="completed",
        item_cursor="fresh-cursor",
    )
    restarted, client = run_snapshot(
        second_client,
        load_actions().ThreadSnapshotRequest(
            target="local",
            cwd="/work",
            thread_id="thread-1",
            revision=first.result.result["revision"],
        ),
    )
    snapshot = restarted.result.result

    assert snapshot["changed"] is True
    assert snapshot["thread_status"] == "idle"
    assert snapshot["latest_turn"]["status"] == "completed"
    assert snapshot["continuation"]["item_cursor"] == "fresh-cursor"
    assert [method for method, _ in client.calls] == [
        "thread/read",
        "thread/turns/list",
        "thread/items/list",
    ]
    assert all("cursor" not in params for method, params in client.calls)
