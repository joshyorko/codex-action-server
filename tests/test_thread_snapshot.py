"""Bounded native supervisory snapshots preserve identity and status provenance."""

import json
from unittest.mock import patch

import pytest
from test_actions import load_actions

import codex_rpc


class SnapshotClient:
    def __init__(self, _target=None, *, status=None, cwd="/work", text="latest "):
        self.calls = []
        self.events = [{"method": "large-native-event", "payload": "x" * 100_000}]
        self.receipts = [{"result": "x" * 100_000}]
        self.metadata = {"userAgent": "codex-cli 0.160.1"}
        self.status = status or {
            "type": "active",
            "activeFlags": ["waitingOnApproval", "waitingOnUserInput"],
        }
        self.cwd = cwd
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def provenance(self):
        return {"target": "local", "codexBin": "codex", "server": "fixture"}

    def request(self, method, params):
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
                        "status": "failed",
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
                            "phase": "final",
                            "text": self.text,
                        },
                    }
                ],
                "nextCursor": "next-item",
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
