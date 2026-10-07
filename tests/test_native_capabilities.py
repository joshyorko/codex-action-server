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
            "0.159.2",
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
