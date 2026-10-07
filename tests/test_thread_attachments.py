"""Thread attachment actions use exact native identities and bounded payloads."""

from unittest.mock import patch

import pytest

from test_actions import FakeClient, load_actions


class AttachmentClient(FakeClient):
    def request(self, method, params):
        self.calls.append((method, params))
        if method == "thread/read":
            return {"thread": {"id": params["threadId"], "cwd": "/trusted"}}
        if method == "thread/attachment/list":
            return {"data": [], "nextCursor": None}
        if method == "thread/attachment/add":
            return {
                "outcome": "created",
                "attachment": {
                    "id": "attachment-1",
                    "attachmentType": params["attachmentType"],
                    "identityKey": params["identityKey"],
                    "payload": params["payload"],
                },
            }
        if method == "thread/attachment/remove":
            return {}
        raise AssertionError(method)


def test_thread_attachments_use_typed_mappings_and_receipts(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_ACTION_RECEIPTS", str(tmp_path))
    module = load_actions()
    client = AttachmentClient(None)
    with patch.object(module, "Client", return_value=client):
        listed = module.list_thread_attachments(
            module.ThreadAttachmentListRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                limit=12,
                cursor="attachment-next",
            )
        ).result.result
        added = module.add_thread_attachment(
            module.ThreadAttachmentAddRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                request_id="attachment-add",
                attachment_type="artifact",
                identity_key="artifact-1",
                payload={"name": "summary", "value": "verified"},
            )
        ).result.result
        removed = module.remove_thread_attachment(
            module.ThreadAttachmentRemoveRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                request_id="attachment-remove",
                attachment_type="artifact",
                identity_key="artifact-1",
            )
        ).result.result

    assert listed["data"] == []
    assert added["attachment"]["id"] == "attachment-1"
    assert added["dispatch"]["state"] == "accepted"
    assert removed["dispatch"]["state"] == "accepted"
    assert [method for method, _params in client.calls if method != "thread/read"] == [
        "thread/attachment/list",
        "thread/attachment/add",
        "thread/attachment/remove",
    ]
    assert client.calls[1][1] == {
        "threadId": "thread-1",
        "limit": 12,
        "cursor": "attachment-next",
    }
    assert client.calls[3][1] == {
        "threadId": "thread-1",
        "attachmentType": "artifact",
        "identityKey": "artifact-1",
        "payload": {"name": "summary", "value": "verified"},
    }


def test_attachment_add_rejects_wrong_worktree_before_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_ACTION_RECEIPTS", str(tmp_path))
    module = load_actions()
    client = AttachmentClient(None)
    original = client.request

    def mismatch(method, params):
        if method == "thread/read":
            client.calls.append((method, params))
            return {"thread": {"id": params["threadId"], "cwd": "/other"}}
        return original(method, params)

    client.request = mismatch
    request = module.ThreadAttachmentAddRequest(
        target="local",
        cwd="/trusted",
        thread_id="thread-1",
        request_id="attachment-add",
        attachment_type="artifact",
        identity_key="artifact-1",
        payload={"name": "summary"},
    )

    with patch.object(module, "Client", return_value=client):
        result = module.add_thread_attachment(request).result.result

    assert [method for method, _params in client.calls] == ["thread/read"]
    assert result["dispatch"]["state"] == "unknown"


def test_attachment_payload_is_limited_to_json_objects_under_16_kib():
    module = load_actions()
    with pytest.raises(ValueError, match="16 KiB"):
        module.ThreadAttachmentAddRequest(
            target="local",
            cwd="/trusted",
            thread_id="thread-1",
            request_id="large-attachment",
            attachment_type="artifact",
            identity_key="large",
            payload={"data": "x" * 16_385},
        )
