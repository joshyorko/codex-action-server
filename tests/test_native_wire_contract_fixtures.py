"""The pinned typed RPC fixture covers every newly qualified method."""

from action_catalog_contract import ACTION_NAMES_BY_PROFILE
from native_capabilities import OBSERVE_METHODS, OPERATOR_CONTROL_METHODS
from native_wire_contracts import (
    CONTRACTS,
    assert_contract_value,
    assert_native_request_contract,
)
import codex_rpc

from pathlib import Path
import json


PROTOCOL_INVENTORY = json.loads(
    (
        Path(__file__).parent / "fixtures/protocol/codex_0.160.1_client_requests.json"
    ).read_text()
)


def test_pinned_wire_fixture_has_request_and_response_contracts():
    expected = OBSERVE_METHODS | OPERATOR_CONTROL_METHODS
    assert set(CONTRACTS) == expected
    assert expected == codex_rpc.METHODS
    assert ACTION_NAMES_BY_PROFILE["observe"] < ACTION_NAMES_BY_PROFILE["operator"]
    for contract in CONTRACTS.values():
        assert contract["native_request_type"]
        assert contract["native_response_type"]
        assert contract["native_protocol_source"]
        assert contract["native_request_contract_scope"] in {
            "native-schema",
            "cas-safe-subset",
        }
        for direction in ("params", "response"):
            schema = contract[direction]
            assert schema.get("type") == "object"
            assert set(schema.get("required", ())) <= set(schema.get("properties", {}))


def test_settings_contracts_are_explicitly_narrow_and_reject_unexposed_fields():
    subset_methods = {"thread/settings/update", "turn/settings/update"}
    assert {
        method
        for method, contract in CONTRACTS.items()
        if contract["native_request_contract_scope"] == "cas-safe-subset"
    } == subset_methods

    invalid_requests = (
        (
            "thread/settings/update",
            {
                "threadId": "thread-1",
                "effort": "high",
                "sandboxPolicy": "danger-full-access",
            },
        ),
        (
            "turn/settings/update",
            {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "model": "gpt-6-luna",
                "summary": "detailed",
            },
        ),
    )
    for method, params in invalid_requests:
        try:
            assert_native_request_contract(method, params)
        except AssertionError as error:
            assert "outside its CAS-safe subset" in str(error)
        else:
            raise AssertionError(
                f"{method} accepted a native field outside its typed CAS subset"
            )


def test_each_exposed_action_method_matches_the_pinned_request_inventory_and_gate():
    default = set(PROTOCOL_INVENTORY["default_client_requests"])
    experimental_only = set(PROTOCOL_INVENTORY["experimental_only_client_requests"])
    exposed = OBSERVE_METHODS | OPERATOR_CONTROL_METHODS
    gated = {
        method for method in exposed if "experimental_api_gate" in CONTRACTS[method]
    }

    assert PROTOCOL_INVENTORY["schema_version"] == "0.160.1"
    assert default & exposed | experimental_only & exposed == exposed
    assert gated == (experimental_only & exposed) | {"app/read"}
    assert codex_rpc.EXPERIMENTAL_METHODS == gated
    assert {
        method
        for method, contract in CONTRACTS.items()
        if contract["native_request_class"] == "experimental-only"
    } == experimental_only & exposed
    assert "app/read" in default
    assert CONTRACTS["app/read"]["native_request_class"] == "default"
    assert CONTRACTS["app/read"]["experimental_api_gate"] == "codex-cli 0.160.1"


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
