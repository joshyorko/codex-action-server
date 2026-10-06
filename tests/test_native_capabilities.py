"""Pinned native inventory and public action contract tests."""

import json
from pathlib import Path

import pytest
from test_actions import FakeClient, load_actions
from unittest.mock import patch

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


def test_inventory_is_versioned_and_classifies_exposure_without_expansion():
    data = inventory("codex-cli 0.160.1")

    assert data["native_codex_version"] == "codex-cli 0.160.1"
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


def test_exposed_native_methods_require_explicit_classification():
    assert not OBSERVE_METHODS & OPERATOR_CONTROL_METHODS
    assert OBSERVE_METHODS | OPERATOR_CONTROL_METHODS == codex_rpc.METHODS
    assert classify_exposed_method("future/nativeMethod") is None
    with pytest.raises(ValueError, match="lack an explicit classification"):
        validate_exposed_methods({"future/nativeMethod"})


def test_capability_action_reports_runtime_version_and_profile():
    module = load_actions()
    client = FakeClient(None)
    client.metadata["userAgent"] = "codex-cli 0.160.1"

    with patch.object(module, "Client", return_value=client):
        response = module.list_native_capabilities(
            module.NativeCapabilitiesRequest(target="local")
        )

    result = response.result.result
    assert result["native_codex_version"] == "codex-cli 0.160.1"
    assert result["server_exposure_profile"] == "operator"
