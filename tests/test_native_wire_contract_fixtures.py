"""The pinned typed RPC fixture covers every newly qualified method."""

from native_wire_contracts import CONTRACTS, assert_contract_value


def test_pinned_wire_fixture_has_request_and_response_contracts():
    expected = {
        "app/read",
        "thread/section/move",
        "thread/queue/add",
        "thread/queue/list",
        "thread/queue/update",
        "thread/queue/delete",
        "thread/queue/reorder",
        "thread/queue/start",
        "thread/search",
        "thread/searchOccurrences",
        "thread/timeline/list",
        "thread/backgroundTerminals/list",
        "thread/backgroundTerminals/terminate",
    }
    assert set(CONTRACTS) == expected
    for contract in CONTRACTS.values():
        for direction in ("params", "response"):
            schema = contract[direction]
            assert set(schema["required"]) <= set(schema["properties"])


def test_pinned_wire_fixture_response_shapes_are_valid():
    samples = {
        "app/read": {
            "apps": [
                {
                    "id": "docs",
                    "name": "Docs",
                    "toolSummaries": [
                        {"name": "search", "description": "Search", "isEnabled": True}
                    ],
                }
            ],
            "missingAppIds": [],
        },
        "thread/section/move": {},
        "thread/queue/add": {
            "queuedSubmission": {
                "id": "queue-1",
                "input": [],
                "clientUserMessageId": "message-1",
            }
        },
        "thread/queue/list": {"data": [], "nextCursor": None},
        "thread/queue/update": {
            "queuedSubmission": {
                "id": "queue-1",
                "input": [],
                "clientUserMessageId": "message-1",
            }
        },
        "thread/queue/delete": {"deleted": True},
        "thread/queue/reorder": {},
        "thread/queue/start": {"turn": {}},
        "thread/search": {
            "data": [],
            "nextCursor": None,
            "backwardsCursor": None,
        },
        "thread/searchOccurrences": {
            "data": [
                {
                    "turnId": "turn-1",
                    "itemId": "item-1",
                    "snippet": "match",
                    "snippetMatchRange": {"start": 0, "end": 5},
                    "turnCursor": "cursor",
                }
            ],
            "nextCursor": None,
        },
        "thread/timeline/list": {
            "data": [
                {
                    "type": "item",
                    "position": 0,
                    "turnId": "turn-1",
                    "item": {},
                },
                {"type": "realtime", "position": 1, "item": {}},
                {
                    "type": "turnStarted",
                    "position": 2,
                    "turnId": "turn-2",
                    "startedAt": None,
                },
                {
                    "type": "turnCompleted",
                    "position": 3,
                    "turnId": "turn-2",
                    "status": "completed",
                    "error": None,
                    "startedAt": None,
                    "completedAt": None,
                    "durationMs": None,
                },
            ],
            "nextCursor": None,
            "activeRealtimeSessionAtPageStart": None,
        },
        "thread/backgroundTerminals/list": {
            "data": [],
            "nextCursor": None,
        },
        "thread/backgroundTerminals/terminate": {"terminated": True},
    }
    for method, response in samples.items():
        assert_contract_value(CONTRACTS[method]["response"], response)
