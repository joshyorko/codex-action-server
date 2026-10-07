"""Pinned native inventory and public action contract tests."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from test_actions import FakeClient, load_actions
from unittest.mock import patch
from native_test_helpers import native_server_user_agent

import codex_rpc
from native_capabilities import (
    OPERATOR_CONTROL_METHODS,
    OBSERVE_METHODS,
    classify_exposed_method,
    inventory,
    validate_exposed_methods,
)

SCHEMA_MANIFEST = json.loads(
    (
        Path(__file__).parent / "fixtures/protocol/codex_0.160.1_client_requests.json"
    ).read_text()
)
SCHEMA_COMPATIBILITY = json.loads(
    (
        Path(__file__).parent
        / "fixtures/protocol/codex_0.160.1_to_0.161.0_experimental_compatibility.json"
    ).read_text()
)

ACTUAL_PINNED_SERVER_USER_AGENT = native_server_user_agent("0.160.1")


@pytest.mark.parametrize(
    ("user_agent", "expected"),
    [
        (ACTUAL_PINNED_SERVER_USER_AGENT, "0.160.1"),
        (
            native_server_user_agent("0.159.2", client_version="0.160.1"),
            "0.159.2",
        ),
        (
            native_server_user_agent(
                "0.159.2",
                originator="spoofed/0.160.1",
                client_name="spoofed/0.160.1",
                client_version="0.160.1",
            ),
            None,
        ),
        (
            native_server_user_agent(
                "0.159.2",
                originator="native client (spoofed/0.160.1 (Linux Unknown; x86_64) unknown)",
            ),
            None,
        ),
        (
            native_server_user_agent(
                "0.159.2",
                originator="spoofed/0.160.1 (Linux Unknown; x86_64) unknown",
            ),
            None,
        ),
        (
            native_server_user_agent(
                "0.160.01",
                originator="spoofed/0.160.1 (Linux Unknown; x86_64) unknown",
            ),
            None,
        ),
        (
            native_server_user_agent("0.160.1", originator="native client (desktop)"),
            None,
        ),
        (
            native_server_user_agent(
                "0.160.1", originator="friday-validation-native-fixture"
            ),
            "0.160.1",
        ),
        (
            native_server_user_agent("0.160.1-beta.1"),
            "0.160.1-beta.1",
        ),
        (
            native_server_user_agent("0.160.2"),
            "0.160.2",
        ),
        (
            native_server_user_agent("0.160.1+build.2"),
            "0.160.1+build.2",
        ),
        ("codex-cli 0.160.1", None),
        (native_server_user_agent("0.160.01"), None),
        (native_server_user_agent("0.160.1١"), None),
        ("friday-external-codex/0.160.1", None),
        (None, None),
    ],
)
def test_native_server_build_version_comes_from_initialize_build_slot(
    user_agent, expected
):
    assert codex_rpc.native_server_build_version(user_agent) == expected


def test_native_server_build_version_ignores_product_user_agent_versions():
    user_agent = (
        "codex-tui/0.161.0 (Linux Unknown; x86_64) "
        "ghostty/1.3.1 (friday-external-codex; 0.1.0)"
    )

    assert codex_rpc.native_server_build_version(user_agent) == "0.161.0"


def test_experimental_gate_uses_server_build_version_not_client_info_suffix():
    def client_with_user_agent(user_agent):
        client = codex_rpc.Client(codex_rpc.Target("local", socket_path="fixture"))
        client.ws = Mock()
        client.metadata = {"userAgent": user_agent}
        client.ws.recv.return_value = json.dumps({"id": 1, "result": {}})
        return client

    pinned = client_with_user_agent(ACTUAL_PINNED_SERVER_USER_AGENT)
    assert pinned.request("server/diagnostics", {}) == {}
    pinned.ws.send.assert_called_once()

    spoofed_client_suffix = client_with_user_agent(
        native_server_user_agent("0.159.2", client_version="0.160.1")
    )
    with pytest.raises(codex_rpc.RpcError, match="pinned experimental"):
        spoofed_client_suffix.request("server/diagnostics", {})
    spoofed_client_suffix.ws.send.assert_not_called()

    spoofed_originator = client_with_user_agent(
        native_server_user_agent(
            "0.159.2",
            originator="spoofed/0.160.1",
            client_name="spoofed/0.160.1",
            client_version="0.160.1",
        )
    )
    with pytest.raises(codex_rpc.RpcError, match="pinned experimental"):
        spoofed_originator.request("server/diagnostics", {})
    spoofed_originator.ws.send.assert_not_called()

    ambiguous_originator = client_with_user_agent(
        native_server_user_agent(
            "0.159.2",
            originator="spoofed/0.160.1 (Linux Unknown; x86_64) unknown",
        )
    )
    with pytest.raises(codex_rpc.RpcError, match="pinned experimental"):
        ambiguous_originator.request("server/diagnostics", {})
    ambiguous_originator.ws.send.assert_not_called()

    for non_pinned in (
        native_server_user_agent("0.160.1-beta.1"),
        native_server_user_agent("0.160.2"),
        native_server_user_agent("0.160.1+build.2"),
        "malformed user agent with codex-cli 0.160.1",
    ):
        client = client_with_user_agent(non_pinned)
        with pytest.raises(codex_rpc.RpcError, match="pinned experimental"):
            client.request("server/diagnostics", {})
        client.ws.send.assert_not_called()


def test_inventory_is_versioned_and_classifies_exposure_without_expansion():
    data = inventory("0.160.1")

    assert data["native_codex_version"] == "0.160.1"
    assert data["native_schema_version"] == "0.160.1"
    assert data["cas_contract_version"] == "0.1.0"
    assert data["schema_request_counts"] == {
        "default_client_requests": 104,
        "experimental_client_requests": 167,
        "experimental_only_client_requests": 63,
    }
    assert data["server_exposure_profile"] == "operator"
    assert data["admin_enabled"] is False
    classifications = {family["classification"] for family in data["families"]}
    assert {
        "OBSERVE",
        "OPERATOR_CONTROL",
        "ADMIN",
        "CALLBACK",
        "DEFERRED/UNSUPPORTED",
        "EXPERIMENTAL",
    } <= classifications


def test_supported_inventory_matches_rpc_allowlist_and_admin_is_never_exposed():
    data = inventory(None)
    supported = {
        method
        for family in data["families"]
        if family["status"] == "supported"
        for method in family["methods"]
    }
    admin = next(
        family["methods"]
        for family in data["families"]
        if family["classification"] == "ADMIN"
    )

    assert supported == codex_rpc.METHODS
    assert not supported.intersection(admin)


def test_pinned_schema_manifest_is_completely_classified():
    families = inventory(None)["families"]
    classified = {method for family in families for method in family["methods"]}
    experimental = next(
        family["methods"]
        for family in families
        if family["classification"] == "EXPERIMENTAL"
    )
    exposed_experimental = next(
        family["exposed_methods"]
        for family in families
        if family["classification"] == "EXPERIMENTAL"
    )
    default = set(SCHEMA_MANIFEST["default_client_requests"])
    experimental_only = set(SCHEMA_MANIFEST["experimental_only_client_requests"])

    assert SCHEMA_MANIFEST["schema_version"] == "0.160.1"
    assert len(default) == 104
    assert len(default | experimental_only) == 167
    assert len(experimental_only) == 63
    assert default <= classified
    assert set(experimental) == experimental_only | {"app/read"}
    assert set(exposed_experimental) == (experimental_only & codex_rpc.METHODS) | {
        "app/read"
    }
    assert codex_rpc.EXPERIMENTAL_METHODS <= set(exposed_experimental)


def test_protocol_compatibility_fixture_covers_every_experimental_cas_method():
    contracts = {
        contract["method"]: contract
        for contract in SCHEMA_COMPATIBILITY["cas_experimental_contracts"]
    }

    assert set(contracts) == codex_rpc.EXPERIMENTAL_METHODS
    assert all(
        contract["request_contract"] == "unchanged" for contract in contracts.values()
    )
    assert {
        method
        for method, contract in contracts.items()
        if contract["response_contract"] != "unchanged"
    } == {
        "thread/queue/start",
        "thread/search",
        "thread/timeline/list",
    }
    for contract in contracts.values():
        assert (
            contract["request_sha256"]["0.160.1"]
            == contract["request_sha256"]["0.161.0"]
        )
        response_hashes = contract["response_sha256"]
        assert (response_hashes["0.160.1"] == response_hashes["0.161.0"]) == (
            contract["response_contract"] == "unchanged"
        )
    assert SCHEMA_COMPATIBILITY["request_counts"] == {
        "0.160.1": {"default": 104, "experimental": 167},
        "0.161.0": {"default": 104, "experimental": 169},
    }
    assert SCHEMA_COMPATIBILITY["new_experimental_only_methods_not_exposed_by_cas"] == [
        "account/bedrock/checkGovCloudRequirements",
        "thread/prediction/request",
    ]


def test_internal_message_board_names_are_not_public_app_server_methods():
    inventory_methods = set(SCHEMA_MANIFEST["default_client_requests"]) | set(
        SCHEMA_MANIFEST["experimental_only_client_requests"]
    )
    exposed_methods = set(codex_rpc.METHODS)
    internal_only_prefixes = (
        "agent_message_board/",
        "multi_agent/",
        "multi_agent_v2/",
    )

    assert not any(
        method.startswith(internal_only_prefixes)
        for method in inventory_methods | exposed_methods
    )


def test_exposed_native_methods_require_explicit_classification():
    assert not OBSERVE_METHODS & OPERATOR_CONTROL_METHODS
    assert OBSERVE_METHODS | OPERATOR_CONTROL_METHODS == codex_rpc.METHODS
    assert classify_exposed_method("future/nativeMethod") is None
    with pytest.raises(ValueError, match="lack an explicit classification"):
        validate_exposed_methods({"future/nativeMethod"})


def test_default_union_app_read_is_version_gated_by_schema_annotation():
    client = codex_rpc.Client(codex_rpc.Target("local", socket_path="fixture"))
    client.ws = Mock()
    client.metadata = {"userAgent": native_server_user_agent("0.153.4")}
    client.ws.recv.return_value = json.dumps(
        {"id": 1, "result": {"apps": [], "missingAppIds": ["docs"]}}
    )

    assert "app/read" in codex_rpc.EXPERIMENTAL_METHODS
    with pytest.raises(codex_rpc.RpcError, match="pinned experimental"):
        client.request("app/read", {"appIds": ["docs"]})
    client.ws.send.assert_not_called()

    client.metadata = {"userAgent": ACTUAL_PINNED_SERVER_USER_AGENT}
    assert client.request("app/read", {"appIds": ["docs"]}) == {
        "apps": [],
        "missingAppIds": ["docs"],
    }
    client.ws.send.assert_called_once()


def test_every_experimental_only_exposed_method_is_version_gated_before_send():
    experimental_only = set(SCHEMA_MANIFEST["experimental_only_client_requests"])
    exposed = OBSERVE_METHODS | OPERATOR_CONTROL_METHODS
    expected = (experimental_only & exposed) | {"app/read"}

    assert codex_rpc.EXPERIMENTAL_METHODS == expected
    samples = {
        "server/diagnostics": {},
        "thread/settings/update": {"threadId": "thread-1", "effort": "high"},
        "turn/settings/update": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "effort": "high",
        },
    }
    for method, params in samples.items():
        client = codex_rpc.Client(codex_rpc.Target("local", socket_path="fixture"))
        client.ws = Mock()
        client.metadata = {"userAgent": native_server_user_agent("0.159.2")}

        with pytest.raises(codex_rpc.RpcError, match="pinned experimental"):
            client.request(method, params)

        client.ws.send.assert_not_called()
        client.metadata = {"userAgent": ACTUAL_PINNED_SERVER_USER_AGENT}
        client.ws.recv.return_value = json.dumps({"id": 1, "result": {}})
        assert client.request(method, params) == {}
        client.ws.send.assert_called_once()


def test_01610_allows_only_reviewed_diagnostics_and_queue_methods():
    compatible = {
        "server/diagnostics": {},
        "thread/queue/list": {"threadId": "thread-1"},
        "thread/queue/add": {
            "threadId": "thread-1",
            "input": [{"type": "text", "text": "queued"}],
            "clientUserMessageId": "message-1",
        },
        "thread/queue/update": {
            "threadId": "thread-1",
            "queuedSubmissionId": "queue-1",
            "input": [{"type": "text", "text": "updated"}],
        },
        "thread/queue/delete": {
            "threadId": "thread-1",
            "queuedSubmissionId": "queue-1",
        },
        "thread/queue/reorder": {
            "threadId": "thread-1",
            "queuedSubmissionIds": ["queue-1"],
        },
        "thread/queue/start": {
            "threadId": "thread-1",
            "queuedSubmissionId": "queue-1",
        },
    }
    user_agent = (
        "codex-tui/0.161.0 (Linux Unknown; x86_64) "
        "ghostty/1.3.1 (friday-external-codex; 0.1.0)"
    )

    for method, params in compatible.items():
        client = codex_rpc.Client(codex_rpc.Target("local", socket_path="fixture"))
        client.ws = Mock()
        client.metadata = {"userAgent": user_agent}
        client.ws.recv.return_value = json.dumps({"id": 1, "result": {}})

        assert client.request(method, params) == {}
        client.ws.send.assert_called_once()

    deferred = codex_rpc.Client(codex_rpc.Target("local", socket_path="fixture"))
    deferred.ws = Mock()
    deferred.metadata = {"userAgent": user_agent}

    with pytest.raises(codex_rpc.RpcError, match="pinned experimental"):
        deferred.request(
            "thread/settings/update", {"threadId": "thread-1", "effort": "high"}
        )

    deferred.ws.send.assert_not_called()


def test_01610_unexposed_experimental_method_stays_outside_typed_surface():
    client = codex_rpc.Client(codex_rpc.Target("local", socket_path="fixture"))
    client.ws = Mock()
    client.metadata = {
        "userAgent": (
            "codex-tui/0.161.0 (Linux Unknown; x86_64) "
            "ghostty/1.3.1 (friday-external-codex; 0.1.0)"
        )
    }

    with pytest.raises(ValueError, match="outside the typed native surface"):
        client.request("collaborationMode/list", {})

    client.ws.send.assert_not_called()


def test_capability_action_reports_runtime_version_and_profile():
    module = load_actions()
    client = FakeClient(None)
    client.metadata["userAgent"] = ACTUAL_PINNED_SERVER_USER_AGENT

    with patch.object(module, "Client", return_value=client):
        response = module.list_native_capabilities(
            module.NativeCapabilitiesRequest(target="local")
        )

    result = response.result.result
    assert result["native_codex_version"] == "0.160.1"
    assert result["server_exposure_profile"] == "operator"


def test_inventory_reports_selected_control_package_and_actual_guard_methods(
    monkeypatch,
):
    monkeypatch.setenv("CODEX_ACTION_PACKAGES", "codex-control")
    data = inventory(None)
    assert data["selected_packages"] == ["codex-control"]
    exposed = {
        family["classification"]: set(family.get("exposed_methods", ()))
        for family in data["families"]
    }
    assert "thread/read" in exposed["OBSERVE"]
    assert "account/usage/read" not in exposed["OBSERVE"]
    assert "turn/start" in exposed["OPERATOR_CONTROL"]
    assert "app/read" not in exposed["EXPERIMENTAL"]
    assert exposed["ADMIN"] == set()
    assert exposed["CALLBACK"] == set()


def test_inventory_reports_observe_package_without_control_methods(monkeypatch):
    monkeypatch.setenv("CODEX_ACTION_PACKAGES", "codex-observe")
    data = inventory(None)
    assert data["selected_packages"] == ["codex-observe"]
    control = next(
        family
        for family in data["families"]
        if family["classification"] == "OPERATOR_CONTROL"
    )
    assert control["exposed_methods"] == []
