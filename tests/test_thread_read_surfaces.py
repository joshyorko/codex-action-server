"""Bounded thread search, section, and timeline wire contracts."""

import json
from unittest.mock import Mock, patch

import pytest

import codex_rpc
from test_actions import FakeClient, load_actions
from native_wire_contracts import (
    assert_native_request_contract,
    assert_native_response_contract,
)


class ThreadReadClient(FakeClient):
    def request(self, method, params):
        self.calls.append((method, params))
        if method == "thread/read":
            return {"thread": {"id": params["threadId"], "cwd": "/trusted"}}
        if method == "threadSection/list":
            return {"data": [], "nextCursor": None}
        if method == "thread/search":
            return {
                "data": [
                    {
                        "thread": {"id": "thread-1", "cwd": "/trusted"},
                        "snippet": "selected",
                    },
                    {
                        "thread": {"id": "thread-2", "cwd": "/other"},
                        "snippet": "filtered",
                    },
                ],
                "nextCursor": "next",
                "backwardsCursor": None,
            }
        if method == "thread/searchOccurrences":
            return {"data": [], "nextCursor": None}
        if method == "thread/timeline/list":
            return {
                "data": [],
                "nextCursor": None,
                "activeRealtimeSessionAtPageStart": None,
            }
        if method == "thread/search":
            return {"data": [], "nextCursor": None, "backwardsCursor": None}
        raise AssertionError(method)


def test_bounded_read_actions_map_exact_native_fields():
    module = load_actions()
    client = ThreadReadClient(None)

    with patch.object(module, "Client", return_value=client):
        module.list_thread_sections(
            module.ThreadSectionListRequest(
                target="local", limit=10, cursor="section-next"
            )
        )
        search_result = module.search_threads(
            module.ThreadSearchRequest(
                target="local",
                cwd="/trusted",
                search_term="needle",
                limit=20,
                cursor="search-next",
                sort_key="updated_at",
                sort_direction="asc",
                source_kinds=["cli", "appServer"],
                archived=False,
            )
        )
        occurrences = module.search_thread_occurrences(
            module.ThreadSearchOccurrencesRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                search_term="needle",
                limit=15,
                cursor="occurrence-next",
            )
        )
        timeline = module.list_thread_timeline(
            module.ThreadTimelineListRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                limit=30,
                cursor="timeline-next",
            )
        )

    assert client.calls == [
        ("threadSection/list", {"limit": 10, "cursor": "section-next"}),
        (
            "thread/search",
            {
                "searchTerm": "needle",
                "limit": 20,
                "cursor": "search-next",
                "sortKey": "updated_at",
                "sortDirection": "asc",
                "sourceKinds": ["cli", "appServer"],
                "archived": False,
            },
        ),
        ("thread/read", {"threadId": "thread-1", "includeTurns": False}),
        (
            "thread/searchOccurrences",
            {
                "threadId": "thread-1",
                "searchTerm": "needle",
                "limit": 15,
                "cursor": "occurrence-next",
            },
        ),
        ("thread/read", {"threadId": "thread-1", "includeTurns": False}),
        (
            "thread/timeline/list",
            {
                "threadId": "thread-1",
                "limit": 30,
                "cursor": "timeline-next",
            },
        ),
    ]
    assert search_result.result.result["data"] == [
        {
            "thread": {"id": "thread-1", "cwd": "/trusted"},
            "snippet": "selected",
        }
    ]
    assert search_result.result.receipts == []
    for method, params in client.calls:
        if method in {
            "thread/search",
            "thread/searchOccurrences",
            "thread/timeline/list",
        }:
            assert_native_request_contract(method, params)
    assert_native_response_contract("thread/search", search_result.result.result)
    assert_native_response_contract(
        "thread/searchOccurrences", occurrences.result.result
    )
    assert_native_response_contract("thread/timeline/list", timeline.result.result)


def test_timeline_rejects_thread_cwd_mismatch_before_native_timeline_request():
    module = load_actions()
    client = ThreadReadClient(None)

    def mismatch(_method, params):
        client.calls.append(("thread/read", params))
        return {"thread": {"id": params["threadId"], "cwd": "/other"}}

    client.request = mismatch
    with patch.object(module, "Client", return_value=client):
        with pytest.raises(module.ActionError, match="thread id/cwd"):
            module.list_thread_timeline(
                module.ThreadTimelineListRequest(
                    target="local",
                    cwd="/trusted",
                    thread_id="thread-1",
                )
            )

    assert client.calls == [
        ("thread/read", {"threadId": "thread-1", "includeTurns": False})
    ]


def test_experimental_search_fails_closed_unless_exact_schema_version_is_advertised():
    client = codex_rpc.Client(codex_rpc.Target("local", socket_path="fixture"))
    client.ws = Mock()
    client.metadata = {"userAgent": "codex-cli 0.153.4"}

    with pytest.raises(codex_rpc.RpcError, match="pinned experimental"):
        client.request("thread/search", {"searchTerm": "needle", "limit": 10})
    client.ws.send.assert_not_called()

    client.metadata = {"userAgent": "codex-cli 0.160.1"}
    client.ws.recv.return_value = json.dumps({"id": 1, "result": {"data": []}})
    assert client.request("thread/search", {"searchTerm": "needle", "limit": 10}) == {
        "data": []
    }
