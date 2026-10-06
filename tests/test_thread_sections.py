"""Thread-section mutations expose only bounded typed operations."""

from unittest.mock import patch

import pytest

from test_actions import FakeClient, load_actions
from native_wire_contracts import (
    assert_native_request_contract,
    assert_native_response_contract,
)


class SectionClient(FakeClient):
    def request(self, method, params):
        self.calls.append((method, params))
        if method == "thread/read":
            return {"thread": {"id": params["threadId"], "cwd": "/trusted"}}
        if method == "threadSection/create":
            return {"section": {"id": "section-1", "name": params["name"]}}
        if method == "threadSection/update":
            return {
                "section": {
                    "id": params["sectionId"],
                    "name": params["name"],
                }
            }
        if method == "threadSection/delete":
            return {}
        if method == "threadSection/list":
            return {"data": [{"id": "section-1", "name": "Review"}], "nextCursor": None}
        if method == "thread/section/move":
            return {}
        raise AssertionError(method)


def test_sections_have_receipt_protected_typed_create_update_delete(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_ACTION_RECEIPTS", str(tmp_path))
    module = load_actions()
    client = SectionClient(None)
    with patch.object(module, "Client", return_value=client):
        created = module.create_thread_section(
            module.ThreadSectionCreateRequest(
                target="local",
                request_id="section-create",
                name="Review",
            )
        ).result.result
        updated = module.update_thread_section(
            module.ThreadSectionUpdateRequest(
                target="local",
                request_id="section-update",
                section_id="section-1",
                name="Reviewed",
            )
        ).result.result
        deleted = module.delete_thread_section(
            module.ThreadSectionDeleteRequest(
                target="local",
                request_id="section-delete",
                section_id="section-1",
            )
        ).result.result

    assert created["section"]["id"] == "section-1"
    assert all(
        value["dispatch"]["state"] == "accepted"
        for value in (created, updated, deleted)
    )
    assert client.calls == [
        ("threadSection/create", {"name": "Review"}),
        (
            "threadSection/update",
            {"sectionId": "section-1", "name": "Reviewed"},
        ),
        ("threadSection/delete", {"sectionId": "section-1"}),
    ]


def test_move_thread_to_section_checks_thread_section_and_anchor_identities(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_ACTION_RECEIPTS", str(tmp_path))
    module = load_actions()
    client = SectionClient(None)
    with patch.object(module, "Client", return_value=client):
        result = module.move_thread_to_section(
            module.ThreadSectionMoveRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                request_id="section-move",
                section_id="section-1",
                before_thread_id="thread-2",
            )
        ).result.result

    assert result["dispatch"]["state"] == "accepted"
    assert client.calls == [
        ("thread/read", {"threadId": "thread-1", "includeTurns": False}),
        ("thread/read", {"threadId": "thread-2", "includeTurns": False}),
        ("threadSection/list", {"limit": 100}),
        (
            "thread/section/move",
            {
                "threadId": "thread-1",
                "sectionId": "section-1",
                "beforeThreadId": "thread-2",
            },
        ),
    ]
    assert_native_request_contract("thread/section/move", client.calls[-1][1])
    assert_native_response_contract("thread/section/move", {})


def test_move_thread_to_section_rejects_unlisted_section_and_self_anchor(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_ACTION_RECEIPTS", str(tmp_path))
    module = load_actions()
    client = SectionClient(None)

    def missing_section(method, params):
        client.calls.append((method, params))
        if method == "thread/read":
            return {"thread": {"id": params["threadId"], "cwd": "/trusted"}}
        if method == "threadSection/list":
            return {"data": [], "nextCursor": None}
        raise AssertionError(method)

    client.request = missing_section
    with patch.object(module, "Client", return_value=client):
        response = module.move_thread_to_section(
            module.ThreadSectionMoveRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                request_id="missing-section",
                section_id="missing",
            )
        ).result.result
    assert response["dispatch"]["state"] == "unknown"
    assert all(method != "thread/section/move" for method, _ in client.calls)

    with pytest.raises(ValueError, match="requires a destination section"):
        module.ThreadSectionMoveRequest(
            target="local",
            cwd="/trusted",
            thread_id="thread-1",
            request_id="remove-section-anchor",
            section_id=None,
            before_thread_id="thread-2",
        )
