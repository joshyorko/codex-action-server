import json
import hashlib
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
GENERATOR_SPEC = importlib.util.spec_from_file_location(
    "generate_native_qualification", ROOT / "ci/generate_native_qualification.py"
)
runtime_gate_generator = importlib.util.module_from_spec(GENERATOR_SPEC)
GENERATOR_SPEC.loader.exec_module(runtime_gate_generator)
BLOCKER_SPEC = importlib.util.spec_from_file_location(
    "report_codex_qualification", ROOT / "ci/report_codex_qualification.py"
)
blocker_report = importlib.util.module_from_spec(BLOCKER_SPEC)
BLOCKER_SPEC.loader.exec_module(blocker_report)
PROPOSAL_SPEC = importlib.util.spec_from_file_location(
    "propose_codex_qualification", ROOT / "ci/propose_codex_qualification.py"
)
qualification_proposal = importlib.util.module_from_spec(PROPOSAL_SPEC)
PROPOSAL_SPEC.loader.exec_module(qualification_proposal)


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


def test_method_qualification_requires_executed_mapped_regression_tests():
    test_path = "tests/test_codex_qualification.py"
    path = ROOT / test_path
    evidence = {
        "schema_version": 1,
        "candidate_version": "0.166.0",
        "status": "passed",
        "test_files": {test_path: qualification.sha256_file(path)},
        "test_outcomes": {test_path: {"passed": 0, "skipped": 3, "failed": 0}},
        "methods": {
            "server/diagnostics": {
                "status": "passed",
                "test_files": [test_path],
                "coverage": {test_path: {"passed": 0, "skipped": 3, "failed": 0}},
            }
        },
    }
    assert (
        qualification._typed_regression_status(
            evidence, "server/diagnostics", "0.166.0", [test_path]
        )
        == "not_run"
    )

    evidence["methods"]["server/diagnostics"]["coverage"][test_path] = {
        "passed": 1,
        "skipped": 3,
        "failed": 0,
    }
    evidence["test_outcomes"][test_path] = {
        "passed": 1,
        "skipped": 3,
        "failed": 0,
    }
    assert (
        qualification._typed_regression_status(
            evidence, "server/diagnostics", "0.166.0", [test_path]
        )
        == "passed"
    )


def test_merged_native_fixture_is_a_schema_baseline_without_runtime_approval(
    tmp_path, monkeypatch
):
    method = "app/read"
    schemas = {
        "AppsReadParams": {
            "type": "object",
            "properties": {"appId": {"type": "string"}},
        },
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


def test_candidate_version_approval_is_not_its_own_compatibility_baseline():
    manifest = json.loads(
        (ROOT / "ci/codex_qualification/approved_methods.json").read_text()
    )
    baseline = qualification._latest_method_contract(
        manifest, "server/diagnostics", "0.166.0"
    )
    assert baseline is not None
    assert baseline[0] == "0.162.1"


def test_first_native_fixture_extends_existing_diagnostics_source(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    artifact = tmp_path / "artifact"
    output = tmp_path / "proposal"
    catalog = json.loads(
        (ROOT / "ci/codex_qualification/native_methods.json").read_text()
    )
    catalog_path = repo / "ci/codex_qualification/native_methods.json"
    catalog_path.parent.mkdir(parents=True)
    catalog_path.write_text(json.dumps(catalog))
    manifest_path = repo / "ci/codex_qualification/approved_methods.json"
    artifact.mkdir()
    schema_dir = artifact / "candidate-schemas"
    schema_dir.mkdir()
    version = "0.166.0"
    source_tag = f"rust-v{version}"
    source_url = (
        f"https://github.com/openai/codex/releases/download/{source_tag}/"
        "codex-x86_64-unknown-linux-musl.tar.gz"
    )
    asset_sha = "a" * 64
    binary_sha = "b" * 64
    asset_id = "42"
    run_url = "https://github.com/joshyorko/codex-action-server/actions/runs/123"
    typed_files = {}
    typed_outcomes = {}
    typed_methods = {}
    method_rows = []
    diagnostic_catalog_row = next(
        row for row in catalog["methods"] if row["method"] == "server/diagnostics"
    )
    schema_fingerprint = {}
    for name in (
        diagnostic_catalog_row["request_schema"],
        diagnostic_catalog_row["response_schema"],
    ):
        schema_path = schema_dir / f"{name}.json"
        schema_path.write_text(
            json.dumps({"title": name, "type": "object"}, indent=2, sort_keys=True)
            + "\n"
        )
        key = (
            "request_sha256"
            if name == diagnostic_catalog_row["request_schema"]
            else "response_sha256"
        )
        schema_fingerprint[key] = hashlib.sha256(schema_path.read_bytes()).hexdigest()

    for catalog_row in catalog["methods"]:
        method = catalog_row["method"]
        tests = sorted(set(catalog_row["regression_tests"]))
        for test_path in tests:
            path = repo / test_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# fixture for {method}\n")
            typed_files[test_path] = hashlib.sha256(path.read_bytes()).hexdigest()
        coverage = {name: {"passed": 1, "skipped": 0, "failed": 0} for name in tests}
        typed_outcomes.update(coverage)
        typed_methods[method] = {
            "status": "passed",
            "test_files": tests,
            "coverage": coverage,
        }
        is_compatible = method == "server/diagnostics"
        method_rows.append(
            {
                "method": method,
                "classification": catalog_row["classification"],
                "request_schema": catalog_row["request_schema"],
                "response_schema": catalog_row["response_schema"],
                "status": "compatible_pending_review"
                if is_compatible
                else "needs_review",
                "reason_code": "schema_and_behavior_passed"
                if is_compatible
                else "reviewed_baseline_missing",
                "schema_fingerprint": schema_fingerprint if is_compatible else None,
                "schema_diff": {"comparison": "test"},
                "typed_regressions": "passed",
                "typed_regression_coverage": coverage,
                "smoke_strategy": catalog_row["smoke_strategy"],
                "smoke": {
                    "status": "passed"
                    if catalog_row["smoke_strategy"] == "disposable_protocol_smoke"
                    else "typed_action_regressions_passed"
                },
                "owning_typed_actions": catalog_row["owning_typed_actions"],
                "regression_tests": catalog_row["regression_tests"],
            }
        )

    provenance = {
        "version": version,
        "source_tag": source_tag,
        "source_url": source_url,
        "source_asset_sha256": asset_sha,
        "source_asset_id": asset_id,
        "binary_sha256": binary_sha,
        "typed_regression_evidence": {
            "schema_version": 1,
            "candidate_version": version,
            "status": "passed",
            "test_files": typed_files,
            "test_outcomes": typed_outcomes,
            "methods": typed_methods,
        },
        "generated_files_sha256": {},
    }
    report = {
        "version": version,
        "source_tag": source_tag,
        "source_url": source_url,
        "source_asset_sha256": asset_sha,
        "source_asset_id": asset_id,
        "binary_sha256": binary_sha,
        "qualification": "compatible_pending_review",
        "methods": method_rows,
        "withheld_methods": [
            row["method"]
            for row in method_rows
            if row["status"] != "compatible_pending_review"
        ],
    }
    (artifact / "provenance.json").write_text(json.dumps(provenance))
    (artifact / "native-methods.json").write_text(json.dumps(report))
    (artifact / "source-release.json").write_text(
        json.dumps(
            {
                "tag_name": source_tag,
                "asset": {
                    "id": int(asset_id),
                    "digest": f"sha256:{asset_sha}",
                    "browser_download_url": source_url,
                },
            }
        )
    )
    diagnostics_fixture = (
        "tests/fixtures/protocol/codex_0.166.0_server_diagnostics.json"
    )
    old_fixture = repo / diagnostics_fixture
    old_fixture.parent.mkdir(parents=True, exist_ok=True)
    old_fixture.write_text(
        json.dumps({"native_version": version, "binary_sha256": binary_sha})
    )
    source = {
        "kind": "diagnostics_fixture",
        "fixture": diagnostics_fixture,
        "source_tag": source_tag,
        "source_url": source_url,
        "source_asset_sha256": asset_sha,
        "source_asset_id": asset_id,
        "binary_sha256": binary_sha,
        "qualification_run": run_url,
    }
    manifest = {
        "schema_version": 1,
        "sources": {version: source},
        "approvals": [
            {
                "version": version,
                "method": "server/diagnostics",
                "provenance_ref": version,
                "schema_fingerprint": schema_fingerprint,
            }
        ],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest))
    docs_path = repo / "docs/acceptance/codex-native-qualification.md"
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    docs_path.write_text("# Qualification\n")
    monkeypatch.setattr(qualification_proposal, "ROOT", repo)
    monkeypatch.setattr(qualification_proposal, "CATALOG", catalog_path)
    monkeypatch.setattr(qualification_proposal, "MANIFEST", manifest_path)
    monkeypatch.setattr(
        qualification_proposal, "_render_runtime_gate", lambda _: "# gate\n"
    )

    result = qualification_proposal.prepare_proposal(artifact, output, run_url)

    assert result["status"] == "prepared"
    assert result["qualified_methods"] == ["server/diagnostics"]
    assert len(result["withheld_methods"]) == len(catalog["methods"]) - 1
    proposed_manifest = json.loads(
        (output / "ci/codex_qualification/approved_methods.json").read_text()
    )
    assert proposed_manifest["sources"][version]["native_methods_fixture"].endswith(
        "_native_methods.json"
    )
    proposed_fixture = json.loads(
        (
            output / proposed_manifest["sources"][version]["native_methods_fixture"]
        ).read_text()
    )
    assert len(proposed_fixture["methods"]) == len(catalog["methods"])
    diagnostics_row = next(
        row
        for row in proposed_fixture["methods"]
        if row["method"] == "server/diagnostics"
    )
    assert (
        diagnostics_row["typed_regression_coverage"][
            "tests/test_codex_qualification.py"
        ]["passed"]
        == 1
    )
    assert (
        sum(
            row["status"] == "compatible_pending_review"
            for row in proposed_fixture["methods"]
        )
        == 1
    )


def test_native_method_fixture_does_not_admit_methods_without_exact_approval(
    tmp_path, monkeypatch
):
    version = "0.166.0"
    method = "app/read"
    fixture_relative = "tests/fixtures/protocol/native_methods.json"
    fixture = {
        "native_version": version,
        "binary_sha256": "a" * 64,
        "qualification_run": "https://github.com/example/repo/actions/runs/123",
        "methods": [
            {
                "method": method,
                "status": "compatible_pending_review",
                "schema_fingerprint": {
                    "request_sha256": "b" * 64,
                    "response_sha256": "c" * 64,
                },
            }
        ],
    }
    fixture_path = tmp_path / fixture_relative
    fixture_path.parent.mkdir(parents=True)
    fixture_path.write_text(json.dumps(fixture))
    source = {
        "kind": "native_methods_fixture",
        "fixture": fixture_relative,
        "native_methods_fixture": fixture_relative,
        "native_methods_qualification_run": fixture["qualification_run"],
        "binary_sha256": fixture["binary_sha256"],
    }
    manifest_path = tmp_path / "ci/codex_qualification/approved_methods.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps({"schema_version": 1, "sources": {version: source}, "approvals": []})
    )
    monkeypatch.setattr(runtime_gate_generator, "ROOT", tmp_path)
    monkeypatch.setattr(runtime_gate_generator, "MANIFEST", manifest_path)

    assert runtime_gate_generator.load_approvals() == {}

    manifest = json.loads(manifest_path.read_text())
    manifest["approvals"] = [
        {
            "version": version,
            "method": method,
            "provenance_ref": version,
            "method_evidence_ref": fixture_relative,
            "schema_fingerprint": fixture["methods"][0]["schema_fingerprint"],
        }
    ]
    manifest_path.write_text(json.dumps(manifest))
    assert runtime_gate_generator.load_approvals() == {version: {method}}


def test_native_method_runtime_approval_rejects_changed_evidence_fingerprint(
    tmp_path, monkeypatch
):
    version = "0.166.0"
    method = "app/read"
    fixture_relative = "tests/fixtures/protocol/native_methods.json"
    fixture = {
        "native_version": version,
        "binary_sha256": "a" * 64,
        "qualification_run": "https://github.com/example/repo/actions/runs/123",
        "methods": [
            {
                "method": method,
                "status": "compatible_pending_review",
                "schema_fingerprint": {
                    "request_sha256": "b" * 64,
                    "response_sha256": "c" * 64,
                },
            }
        ],
    }
    fixture_path = tmp_path / fixture_relative
    fixture_path.parent.mkdir(parents=True)
    fixture_path.write_text(json.dumps(fixture))
    manifest_path = tmp_path / "ci/codex_qualification/approved_methods.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": {
                    version: {
                        "kind": "native_methods_fixture",
                        "fixture": fixture_relative,
                        "native_methods_fixture": fixture_relative,
                        "native_methods_qualification_run": fixture[
                            "qualification_run"
                        ],
                        "binary_sha256": fixture["binary_sha256"],
                    }
                },
                "approvals": [
                    {
                        "version": version,
                        "method": method,
                        "provenance_ref": version,
                        "method_evidence_ref": fixture_relative,
                        "schema_fingerprint": {
                            "request_sha256": "0" * 64,
                            "response_sha256": "c" * 64,
                        },
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(runtime_gate_generator, "ROOT", tmp_path)
    monkeypatch.setattr(runtime_gate_generator, "MANIFEST", manifest_path)

    try:
        runtime_gate_generator.load_approvals()
    except ValueError as error:
        assert "schema fingerprint mismatch" in str(error)
    else:
        raise AssertionError("runtime approval accepted a changed method fingerprint")


def test_blocker_issue_is_deduplicatable_and_does_not_leak_native_payload(tmp_path):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "provenance.json").write_text(
        json.dumps(
            {
                "version": "0.166.0",
                "source_asset_sha256": "a" * 64,
                "binary_sha256": "b" * 64,
            }
        )
    )
    (artifact / "native-methods.json").write_text(
        json.dumps(
            {
                "version": "0.166.0",
                "qualification": "needs_review",
                "source_tag": "rust-v0.166.0",
                "source_url": "https://github.com/openai/codex/releases/download/rust-v0.166.0/codex-x86_64-unknown-linux-musl.tar.gz",
                "binary_sha256": "b" * 64,
                "counts": {"total": 58, "compatible_pending_review": 1},
                "methods": [
                    {
                        "method": "app/read",
                        "status": "needs_review",
                        "reason_code": "reviewed_baseline_missing",
                        "typed_regressions": "passed",
                        "smoke": {"status": "typed_action_regressions_passed"},
                        "response_payload": "private native response must not appear",
                    }
                ],
            }
        )
    )
    run_url = "https://github.com/joshyorko/codex-action-server/actions/runs/123"

    first_title, body = blocker_report.build_report(
        artifact, "0.166.0", run_url, "success"
    )
    second_title, _ = blocker_report.build_report(
        artifact, "0.166.0", run_url, "success"
    )

    assert first_title == second_title
    assert first_title.endswith(": 0.166.0")
    assert "`app/read`" in body
    assert "reviewed_baseline_missing" in body
    assert "private native response must not appear" not in body
