import json
import importlib.util
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "codex_qualification", ROOT / "ci/qualify_codex_schema.py"
)
qualification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qualification)
REGRESSION_SPEC = importlib.util.spec_from_file_location(
    "native_method_regressions", ROOT / "ci/run_native_method_regressions.py"
)
native_method_regressions = importlib.util.module_from_spec(REGRESSION_SPEC)
REGRESSION_SPEC.loader.exec_module(native_method_regressions)


def test_schema_normalization_ignores_only_documentation_noise():
    baseline = {
        "$schema": "draft-07",
        "title": "ServerDiagnosticsResponse",
        "description": "before",
        "type": "object",
        "properties": {"value": {"type": ["integer", "null"]}},
    }
    candidate = {
        "title": "Generated response",
        "description": "after",
        "type": "object",
        "properties": {"value": {"type": ["integer", "null"]}},
    }
    assert qualification.normalize(baseline) == qualification.normalize(candidate)


def test_schema_normalization_preserves_contract_changes():
    baseline = {"type": ["integer", "null"]}
    changed = {"type": "integer"}
    assert qualification.normalize(baseline) != qualification.normalize(changed)


def test_0166_diagnostics_fixture_matches_reviewed_contract():
    candidate = json.loads(
        (
            ROOT / "tests/fixtures/protocol/codex_0.166.0_server_diagnostics.json"
        ).read_text()
    )
    baseline = json.loads(
        (
            ROOT / "tests/fixtures/protocol/codex_0.162.1_server_diagnostics.json"
        ).read_text()
    )
    assert candidate["native_version"] == "0.166.0"
    assert candidate["method"] == "server/diagnostics"
    assert qualification.normalize(candidate["schemas"]) == qualification.normalize(
        baseline["schemas"]
    )


def test_response_schema_validator_checks_types_and_required_fields():
    schema = {
        "type": "object",
        "required": ["process", "gauges"],
        "properties": {
            "process": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "integer"}},
            },
            "gauges": {"type": "array", "items": {"type": "string"}},
        },
    }
    assert qualification.matches_json_schema(
        {"process": {"id": 1}, "gauges": ["threads"]}, schema
    )
    assert not qualification.matches_json_schema(
        {"process": {"id": "1"}, "gauges": ["threads"]}, schema
    )


def test_response_schema_validator_resolves_local_definitions():
    schema = {
        "type": "object",
        "required": ["process"],
        "properties": {"process": {"$ref": "#/definitions/Process"}},
        "definitions": {
            "Process": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "integer"}},
            }
        },
    }
    assert qualification.matches_json_schema({"process": {"id": 42}}, schema)
    assert not qualification.matches_json_schema({"process": {"id": "bad"}}, schema)


def test_method_regression_plan_names_the_full_catalog_and_real_test_files():
    evidence = native_method_regressions.regression_evidence("0.166.0")
    assert evidence["candidate_version"] == "0.166.0"
    assert len(evidence["methods"]) == 58
    assert {
        "tests/test_native_diagnostics_0162.py",
        "tests/test_protocol_contracts.py",
    } <= set(evidence["methods"]["server/diagnostics"]["test_files"])
    assert "tests/test_native_wire_contract_fixtures.py" in evidence["test_files"]


def test_method_qualification_withholds_every_method_when_candidate_schemas_are_missing():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        binary = root / "codex"
        binary.write_bytes(b"isolated qualification test input")
        output = root / "artifact"
        report = qualification.build_method_evidence(
            binary,
            {},
            output,
            version="0.166.0",
            asset_sha256="0" * 64,
            asset_id="1",
            typed_regression_evidence={},
            temporary=root / "smokes",
            diagnostics_smoke={"method": "server/diagnostics", "status": "not_run"},
        )
    assert report["qualification"] == "unqualified"
    assert report["counts"] == {
        "total": 58,
        "compatible_pending_review": 0,
        "needs_review": 0,
        "unqualified": 58,
        "withheld": 58,
    }
    assert len(report["withheld_methods"]) == 58


def test_method_qualification_rejects_stale_typed_regression_evidence():
    test_path = ROOT / "tests/test_codex_qualification.py"
    evidence = {
        "schema_version": 1,
        "candidate_version": "0.166.0",
        "status": "passed",
        "test_files": {"tests/test_codex_qualification.py": "0" * 64},
        "methods": {
            "server/diagnostics": {
                "status": "passed",
                "test_files": ["tests/test_codex_qualification.py"],
            }
        },
    }
    assert test_path.is_file()
    assert (
        qualification._typed_regression_status(
            evidence,
            "server/diagnostics",
            "0.166.0",
            ["tests/test_codex_qualification.py"],
        )
        == "invalid_evidence"
    )


def test_merged_native_fixture_is_a_schema_baseline_without_runtime_approval(
    tmp_path, monkeypatch
):
    method = "app/read"
    schemas = {
        "AppsReadParams": {"type": "object", "properties": {"appId": {"type": "string"}}},
        "AppsReadResponse": {
            "type": "object",
            "required": ["app"],
            "properties": {"app": {"type": "object"}},
        },
    }
    fixture_path = tmp_path / "codex_0.166.0_native_methods.json"
    fixture_path.write_text(
        json.dumps(
            {
                "native_version": "0.166.0",
                "binary_sha256": "a" * 64,
                "typed_regression_status": "passed",
                "methods": [
                    {
                        "method": method,
                        "status": "needs_review",
                        "typed_regressions": "passed",
                        "schema_fingerprint": {
                            "request_sha256": "b" * 64,
                            "response_sha256": "c" * 64,
                        },
                        "schemas": schemas,
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(qualification, "ROOT", tmp_path)
    manifest = {
        "sources": {
            "0.166.0": {
                "kind": "diagnostics_fixture",
                "binary_sha256": "a" * 64,
                "native_methods_fixture": fixture_path.name,
            }
        },
        "approvals": [],
    }
    baseline = qualification._latest_method_contract(manifest, method, "0.167.0")
    assert baseline == (
        "0.166.0",
        {"request_sha256": "b" * 64, "response_sha256": "c" * 64},
        schemas,
    )
