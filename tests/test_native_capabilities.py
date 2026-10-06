"""Pinned native inventory and public action contract tests."""

from test_actions import FakeClient, load_actions
from unittest.mock import patch

import codex_rpc
from native_capabilities import inventory


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
