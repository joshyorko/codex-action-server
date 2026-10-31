"""The reviewed native-method catalog must cover CAS's exact typed surface."""

import json
from pathlib import Path

import codex_rpc
from action_catalog_contract import CAPABILITIES
from native_capabilities import OBSERVE_METHODS


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "ci/codex_qualification/native_methods.json"


def _catalog():
    value = json.loads(CATALOG_PATH.read_text())
    assert value["schema_version"] == 1
    return value["methods"]


def test_catalog_exactly_covers_cas_native_methods_and_owners():
    rows = _catalog()
    by_method = {row["method"]: row for row in rows}
    assert len(rows) == len(by_method)
    assert set(by_method) == codex_rpc.METHODS

    expected_owners = {}
    for action_name, capability in CAPABILITIES.items():
        for method in capability.native_methods:
            expected_owners.setdefault(method, set()).add(action_name)

    for method, row in by_method.items():
        expected_classification = (
            "experimental" if method in codex_rpc.EXPERIMENTAL_METHODS else "stable"
        )
        assert row["classification"] == expected_classification
        assert row["required_capabilities"] == (
            ["experimentalApi"] if method in codex_rpc.EXPERIMENTAL_METHODS else []
        )
        assert row["access"] == (
            "read_only" if method in OBSERVE_METHODS else "mutating_or_external"
        )
        assert set(row["owning_typed_actions"]) == expected_owners[method]
        assert row["request_schema"].endswith("Params")
        assert row["response_schema"].endswith("Response")
        assert row["smoke_strategy"] in {
            "disposable_protocol_smoke",
            "typed_read_fixture",
            "disposable_mutation_fixture",
        }
        assert row["regression_tests"]
        assert all((ROOT / path).is_file() for path in row["regression_tests"])


def test_catalog_check_matches_the_reviewed_source_contract():
    from ci.generate_native_method_catalog import expected_catalog

    assert json.loads(CATALOG_PATH.read_text()) == expected_catalog()
