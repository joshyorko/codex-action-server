"""Typed lifecycle controls must preserve native thread/cwd identity."""

from unittest.mock import patch

from test_actions import FakeClient, load_actions


class LifecycleClient(FakeClient):
    def __init__(self, cwd="/trusted"):
        super().__init__(None)
        self.cwd = cwd

    def request(self, method, params):
        self.calls.append((method, params))
        if method == "thread/read":
            return {"thread": {"id": params["threadId"], "cwd": self.cwd}}
        if method == "thread/fork":
            return {
                "thread": {"id": "thread-fork", "cwd": params["cwd"]},
                "model": "model",
                "modelProvider": "provider",
            }
        if method in {
            "thread/archive",
            "thread/unarchive",
            "thread/delete",
            "thread/name/set",
            "thread/metadata/update",
            "thread/revert",
            "thread/compact/start",
        }:
            return {}
        raise AssertionError(method)


def test_lifecycle_actions_send_only_typed_native_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_ACTION_RECEIPTS", str(tmp_path))
    module = load_actions()
    client = LifecycleClient()
    calls = (
        (
            module.fork_thread,
            module.ThreadForkRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                request_id="fork",
                last_turn_id="turn-1",
                model="model",
                model_provider="provider",
            ),
        ),
        (
            module.archive_thread,
            module.ThreadMutationRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                request_id="archive",
            ),
        ),
        (
            module.set_thread_name,
            module.ThreadNameSetRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                request_id="name",
                name="review",
            ),
        ),
        (
            module.update_thread_metadata,
            module.ThreadMetadataUpdateRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                request_id="metadata",
                project_id="project-1",
                git_info={"branch": "topic", "sha": None},
            ),
        ),
        (
            module.revert_thread,
            module.ThreadRevertRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                request_id="revert",
                before_turn_id="turn-2",
            ),
        ),
    )

    with patch.object(module, "Client", return_value=client):
        results = [function(request).result.result for function, request in calls]

    assert results[0]["thread"]["id"] == "thread-fork"
    assert all(result["dispatch"]["state"] == "accepted" for result in results)
    assert [method for method, _params in client.calls] == [
        "thread/read",
        "thread/fork",
        "thread/read",
        "thread/archive",
        "thread/read",
        "thread/name/set",
        "thread/read",
        "thread/metadata/update",
        "thread/read",
        "thread/revert",
    ]
    assert client.calls[1][1] == {
        "threadId": "thread-1",
        "cwd": "/trusted",
        "excludeTurns": True,
        "lastTurnId": "turn-1",
        "model": "model",
        "modelProvider": "provider",
    }
    assert client.calls[5][1] == {"threadId": "thread-1", "name": "review"}
    assert client.calls[7][1] == {
        "threadId": "thread-1",
        "projectId": "project-1",
        "gitInfo": {"branch": "topic", "sha": None},
    }
    assert client.calls[9][1] == {
        "threadId": "thread-1",
        "beforeTurnId": "turn-2",
    }


def test_lifecycle_identity_mismatch_fails_before_native_mutation(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_ACTION_RECEIPTS", str(tmp_path))
    module = load_actions()
    client = LifecycleClient(cwd="/other")
    request = module.ThreadMutationRequest(
        target="local",
        cwd="/trusted",
        thread_id="thread-1",
        request_id="archive",
    )

    with patch.object(module, "Client", return_value=client):
        result = module.archive_thread(request).result.result

    assert [method for method, _params in client.calls] == ["thread/read"]
    assert result["dispatch"]["state"] == "unknown"


def test_metadata_patch_requires_fields_and_models_reject_extra_rpc_fields():
    module = load_actions()
    base = {
        "target": "local",
        "cwd": "/trusted",
        "thread_id": "thread-1",
        "request_id": "metadata",
    }
    for patch_data in ({}, {"git_info": {}}, {"git_info": {"arbitrary": "value"}}):
        try:
            module.ThreadMetadataUpdateRequest(**base, **patch_data)
        except ValueError:
            continue
        raise AssertionError("invalid metadata patch was accepted")
