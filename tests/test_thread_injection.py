"""Item injection accepts only bounded native user-text messages."""

from unittest.mock import patch

import pytest

from test_actions import FakeClient, load_actions


class InjectionClient(FakeClient):
    def request(self, method, params):
        self.calls.append((method, params))
        if method == "thread/read":
            return {"thread": {"id": params["threadId"], "cwd": "/trusted"}}
        if method == "thread/inject_items":
            return {}
        raise AssertionError(method)


def test_item_injection_maps_only_the_explicit_text_item_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_ACTION_RECEIPTS", str(tmp_path))
    module = load_actions()
    client = InjectionClient(None)
    request = module.ThreadInjectItemsRequest(
        target="local",
        cwd="/trusted",
        thread_id="thread-1",
        request_id="inject-once",
        items=[
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "continue the task"}],
            }
        ],
    )

    with patch.object(module, "Client", return_value=client):
        result = module.inject_thread_items(request).result.result

    assert result["dispatch"]["state"] == "accepted"
    assert client.calls == [
        ("thread/read", {"threadId": "thread-1", "includeTurns": False}),
        (
            "thread/inject_items",
            {
                "threadId": "thread-1",
                "items": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "continue the task"}
                        ],
                    }
                ],
            },
        ),
    ]


def test_item_injection_rejects_unknown_items_and_oversized_text():
    module = load_actions()
    base = {
        "target": "local",
        "cwd": "/trusted",
        "thread_id": "thread-1",
        "request_id": "inject-once",
    }
    with pytest.raises(ValueError):
        module.ThreadInjectItemsRequest(
            **base,
            items=[{"type": "function_call", "name": "arbitrary"}],
        )
    with pytest.raises(ValueError, match="16 KiB"):
        module.ThreadInjectItemsRequest(
            **base,
            items=[
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "x" * 4096}],
                }
                for _ in range(5)
            ],
        )
