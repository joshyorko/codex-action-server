"""Synthetic one-method receipts test evidence freshness, not native acceptance."""

import hashlib
import json

import pytest

from native_compatibility_review import reviewed_schema_change
from test_native_qualification_receipt import _write_receipt_inputs, receipt


FIXTURE = "tests/fixtures/protocol/codex_0.166.0_native_methods.json"
REVIEW = "ci/codex_qualification/reviewed_schema_changes.json"
SOURCE = "src/reader.py"
PROBE = "ci/smoke.py"


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def reviewed_inputs(tmp_path):
    _write_receipt_inputs(tmp_path)
    fixture = json.loads((tmp_path / FIXTURE).read_text())
    row = fixture["methods"][0]
    test_file = tmp_path / "tests/test_protocol_contracts.py"
    test_file.write_text("def test_app_response():\n    assert True\n")
    fixture["typed_regression_test_files_sha256"][
        "tests/test_protocol_contracts.py"
    ] = _digest(test_file)
    source = tmp_path / SOURCE
    source.parent.mkdir()
    source.write_text("def read(value):\n    return value['apps']\n")
    probe = tmp_path / PROBE
    probe.write_text("# synthetic native probe identity; never executed\n")
    review_row = {
        "schema_fingerprint": row["schema_fingerprint"],
        "source_files_sha256": {SOURCE: _digest(source)},
        "tests": ["tests/test_protocol_contracts.py::test_app_response"],
        "summary": "Synthetic consumed-contract review for receipt control flow.",
        "request_fields": [],
        "response_fields": ["apps"],
    }
    document = {
        "schema_version": 1,
        "versions": {
            fixture["native_version"]: {
                **{
                    key: fixture[key]
                    for key in (
                        "native_version",
                        "binary_sha256",
                        "source_tag",
                        "source_url",
                        "source_asset_sha256",
                        "source_asset_id",
                    )
                },
                "methods": {row["method"]: review_row},
            }
        },
    }
    (tmp_path / REVIEW).write_text(json.dumps(document))
    row["reason_code"] = "reviewed_consumed_contract"
    row["compatibility_review"] = reviewed_schema_change(
        tmp_path,
        row["method"],
        fixture["native_version"],
        fixture["binary_sha256"],
        fixture["source_asset_sha256"],
        fixture["source_asset_id"],
        row["schema_fingerprint"],
        row["regression_tests"],
    )
    assert row["compatibility_review"] is not None
    row["reviewed_test_outcomes"] = {
        "tests/test_protocol_contracts.py::test_app_response": {
            "passed": 1,
            "skipped": 0,
            "failed": 0,
        }
    }
    fixture["reviewed_test_node_outcomes"] = row["reviewed_test_outcomes"]
    row["native_positive"] = {
        "status": "passed",
        "positive": True,
        "native_version": fixture["native_version"],
        "binary_sha256": fixture["binary_sha256"],
        **row["schema_fingerprint"],
        "fixture": PROBE,
        "fixture_sha256": _digest(probe),
    }
    (tmp_path / FIXTURE).write_text(json.dumps(fixture))
    return tmp_path, fixture


def _build(root, **options):
    commit = "e" * 40
    return receipt.build_receipt(
        root,
        repository="joshyorko/codex-action-server",
        source_commit=commit,
        image="ghcr.io/joshyorko/codex-action-server",
        image_digest="sha256:" + "f" * 64,
        image_revision=commit,
        qualification_run_url="https://github.com/joshyorko/codex-action-server/actions/runs/123",
        required_ci_url="https://github.com/joshyorko/codex-action-server/actions/runs/124",
        publication_run_url="https://github.com/joshyorko/codex-action-server/actions/runs/125",
        comparison_url=f"https://github.com/joshyorko/codex-action-server/compare/{'d' * 40}...{commit}",
        **options,
    )


def test_synthetic_receipt_accepts_fresh_review_and_positive_probe(reviewed_inputs):
    root, _ = reviewed_inputs
    value = _build(root)
    assert value is not None
    assert value["qualification"]["surface"]["methods"] == [
        {"name": "app/read", "owner": "typed_action"}
    ]


@pytest.mark.parametrize(
    "relative, message",
    [
        (SOURCE, "reviewed source hash is stale"),
        (REVIEW, "compatibility review hash is stale"),
        (PROBE, "positive fixture hash is stale"),
    ],
)
def test_receipt_rejects_changed_reviewed_or_probe_files(
    reviewed_inputs, relative, message
):
    root, _ = reviewed_inputs
    assert _build(root) is not None
    path = root / relative
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match=message):
        _build(root)


def test_receipt_requires_positive_native_evidence_after_semantic_review(
    reviewed_inputs,
):
    root, fixture = reviewed_inputs
    assert _build(root) is not None
    del fixture["methods"][0]["native_positive"]
    (root / FIXTURE).write_text(json.dumps(fixture))
    with pytest.raises(ValueError, match="positive behavior is missing"):
        _build(root)


def test_receipt_cannot_replace_stable_review_with_an_arbitrary_exact_probe(
    reviewed_inputs,
):
    root, fixture = reviewed_inputs
    row = fixture["methods"][0]
    row["method"] = "plugin/read"
    row["classification"] = "stable"
    row["reason_code"] = "reviewed_exact_version_probe"
    row["compatibility_review"] = {
        "kind": "reviewed_exact_version_probe",
        "method": "plugin/read",
        "fixture": SOURCE,
        "fixture_sha256": _digest(root / SOURCE),
    }
    del row["native_positive"]
    (root / FIXTURE).write_text(json.dumps(fixture))
    catalog_path = root / "ci/codex_qualification/native_methods.json"
    catalog = json.loads(catalog_path.read_text())
    catalog["methods"][0].update(method="plugin/read", classification="stable")
    catalog_path.write_text(json.dumps(catalog))
    manifest_path = root / "ci/codex_qualification/approved_methods.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["approvals"] = []
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="exact-version probe"):
        _build(root)


def test_receipt_rejects_a_removed_reviewed_node_even_with_fresh_file_hash(
    reviewed_inputs,
):
    root, fixture = reviewed_inputs
    test_file = root / "tests/test_protocol_contracts.py"
    test_file.write_text("def test_unrelated():\n    assert True\n")
    fixture["typed_regression_test_files_sha256"][
        "tests/test_protocol_contracts.py"
    ] = _digest(test_file)
    (root / FIXTURE).write_text(json.dumps(fixture))
    with pytest.raises(ValueError, match="reviewed source hash is stale"):
        _build(root)


@pytest.mark.parametrize(
    "counts",
    [
        {"passed": 0, "skipped": 0, "failed": 0},
        {"passed": 0, "skipped": 1, "failed": 0},
        {"passed": 1, "skipped": 0, "failed": 1},
    ],
)
def test_receipt_refuses_nonexecuted_skipped_or_failed_reviewed_nodes(
    reviewed_inputs, counts
):
    root, fixture = reviewed_inputs
    fixture["reviewed_test_node_outcomes"][
        "tests/test_protocol_contracts.py::test_app_response"
    ] = counts
    fixture["methods"][0]["reviewed_test_outcomes"] = fixture[
        "reviewed_test_node_outcomes"
    ]
    (root / FIXTURE).write_text(json.dumps(fixture))
    with pytest.raises(ValueError, match="reviewed test nodes did not pass"):
        _build(root)


@pytest.mark.parametrize(
    "field, value",
    [
        ("binary_sha256", "0" * 64),
        ("request_sha256", "0" * 64),
        ("response_sha256", "0" * 64),
        ("native_version", "0.167.0"),
        ("positive", False),
        ("positive", 1),
        ("status", "failed"),
    ],
)
def test_receipt_rejects_positive_evidence_for_different_inputs(
    reviewed_inputs, field, value
):
    root, fixture = reviewed_inputs
    assert _build(root) is not None
    fixture["methods"][0]["native_positive"][field] = value
    (root / FIXTURE).write_text(json.dumps(fixture))
    with pytest.raises(ValueError, match="positive behavior provenance is invalid"):
        _build(root)
