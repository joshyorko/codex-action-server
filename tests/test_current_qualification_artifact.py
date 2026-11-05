"""A merged snapshot cannot overrule partial or stale current-run evidence."""

import json

import pytest

from test_native_qualification_receipt import (
    _write_current_qualification_artifact,
    _write_receipt_inputs,
)
from test_native_receipt_review_bindings import _build
from test_native_receipt_review_bindings import reviewed_inputs as _reviewed_inputs

reviewed_inputs = _reviewed_inputs


def test_current_full_qualification_can_authorize_the_merged_snapshot(tmp_path):
    _write_receipt_inputs(tmp_path)
    artifact = _write_current_qualification_artifact(tmp_path, tmp_path / "artifact")
    assert _build(tmp_path, qualification_artifact=artifact) is not None


def test_current_partial_qualification_cannot_be_replaced_by_complete_merged_json(
    tmp_path,
):
    _write_receipt_inputs(tmp_path)
    artifact = _write_current_qualification_artifact(tmp_path, tmp_path / "artifact")
    report = json.loads((artifact / "native-methods.json").read_text())
    report["methods"][0]["status"] = "needs_review"
    (artifact / "native-methods.json").write_text(json.dumps(report))
    with pytest.raises(
        ValueError, match="current qualification artifact is incomplete"
    ):
        _build(tmp_path, qualification_artifact=artifact)


@pytest.mark.parametrize(
    "key,value",
    [
        ("version", "0.167.0"),
        ("binary_sha256", "0" * 64),
        ("source_asset_sha256", "0" * 64),
    ],
)
def test_current_qualification_requires_the_same_release_identity(tmp_path, key, value):
    _write_receipt_inputs(tmp_path)
    artifact = _write_current_qualification_artifact(tmp_path, tmp_path / "artifact")
    provenance = json.loads((artifact / "provenance.json").read_text())
    provenance[key] = value
    (artifact / "provenance.json").write_text(json.dumps(provenance))
    with pytest.raises(ValueError, match="release provenance mismatch"):
        _build(tmp_path, qualification_artifact=artifact)


def test_current_schema_bytes_must_match_both_the_run_and_merged_fingerprints(tmp_path):
    _write_receipt_inputs(tmp_path)
    artifact = _write_current_qualification_artifact(tmp_path, tmp_path / "artifact")
    (artifact / "candidate-schemas/AppsReadParams.json").write_text('{"type":"string"}')
    with pytest.raises(ValueError, match="current qualification schema differs"):
        _build(tmp_path, qualification_artifact=artifact)


def test_current_run_must_have_executed_the_reviewed_node(reviewed_inputs):
    root, _ = reviewed_inputs
    artifact = _write_current_qualification_artifact(root, root / "artifact")
    assert _build(root, qualification_artifact=artifact) is not None
    provenance = json.loads((artifact / "provenance.json").read_text())
    provenance["typed_regression_evidence"]["test_node_outcomes"] = {
        "tests/test_protocol_contracts.py::test_app_response": {
            "passed": 0,
            "skipped": 1,
            "failed": 0,
        }
    }
    (artifact / "provenance.json").write_text(json.dumps(provenance))
    with pytest.raises(ValueError, match="reviewed test nodes did not pass"):
        _build(root, qualification_artifact=artifact)
