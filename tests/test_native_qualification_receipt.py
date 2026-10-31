"""Downstream receipts tie merged qualification evidence to one image digest."""

import importlib.util
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "native_qualification_receipt", ROOT / "ci/create_native_qualification_receipt.py"
)
receipt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(receipt)


def _write_receipt_inputs(root: Path):
    version = "0.166.0"
    method = "app/read"
    source_tag = "rust-v0.166.0"
    source_url = (
        "https://github.com/openai/codex/releases/download/rust-v0.166.0/"
        "codex-x86_64-unknown-linux-musl.tar.gz"
    )
    fixture_relative = f"tests/fixtures/protocol/codex_{version}_native_methods.json"
    fixture = {
        "schema_version": 1,
        "native_version": version,
        "source_tag": source_tag,
        "source_url": source_url,
        "source_asset_sha256": "a" * 64,
        "source_asset_id": "42",
        "binary_sha256": "b" * 64,
        "qualification_run": "https://github.com/joshyorko/codex-action-server/actions/runs/123",
        "typed_regression_status": "passed",
        "methods": [
            {
                "method": method,
                "classification": "experimental",
                "request_schema": "AppsReadParams",
                "response_schema": "AppsReadResponse",
                "status": "compatible_pending_review",
                "smoke_strategy": "typed_read_fixture",
                "owning_typed_actions": ["read_app"],
                "typed_regressions": "passed",
                "smoke": {"status": "typed_action_regressions_passed"},
                "regression_tests": ["tests/test_protocol_contracts.py"],
                "schema_fingerprint": {
                    "request_sha256": "c" * 64,
                    "response_sha256": "d" * 64,
                },
                "schemas": {
                    "AppsReadParams": {"type": "object"},
                    "AppsReadResponse": {"type": "object"},
                },
            }
        ],
    }
    fixture_path = root / fixture_relative
    fixture_path.parent.mkdir(parents=True)
    fixture_path.write_text(json.dumps(fixture))
    source = {
        "kind": "native_methods_fixture",
        "fixture": fixture_relative,
        "native_methods_fixture": fixture_relative,
        "native_methods_qualification_run": fixture["qualification_run"],
        "source_tag": source_tag,
        "source_url": source_url,
        "source_asset_sha256": "a" * 64,
        "source_asset_id": "42",
        "binary_sha256": "b" * 64,
    }
    manifest = {
        "schema_version": 1,
        "sources": {version: source},
        "approvals": [
            {
                "version": version,
                "method": method,
                "provenance_ref": version,
                "method_evidence_ref": fixture_relative,
                "schema_fingerprint": fixture["methods"][0]["schema_fingerprint"],
            }
        ],
    }
    manifest_path = root / "ci/codex_qualification/approved_methods.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest))
    catalog_path = root / "ci/codex_qualification/native_methods.json"
    catalog_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "methods": [
                    {
                        "method": method,
                        "classification": "experimental",
                        "request_schema": "AppsReadParams",
                        "response_schema": "AppsReadResponse",
                        "smoke_strategy": "typed_read_fixture",
                        "owning_typed_actions": ["read_app"],
                    }
                ],
            }
        )
    )


def test_receipt_binds_merged_qualification_to_one_published_image(tmp_path):
    _write_receipt_inputs(tmp_path)
    commit = "e" * 40
    digest = "sha256:" + "f" * 64
    value = receipt.build_receipt(
        tmp_path,
        repository="joshyorko/codex-action-server",
        source_commit=commit,
        image="ghcr.io/joshyorko/codex-action-server",
        image_digest=digest,
        image_revision=commit,
        qualification_run_url="https://github.com/joshyorko/codex-action-server/actions/runs/123",
        required_ci_url="https://github.com/joshyorko/codex-action-server/actions/runs/124",
        publication_run_url="https://github.com/joshyorko/codex-action-server/actions/runs/125",
        comparison_url=f"https://github.com/joshyorko/codex-action-server/compare/{'d' * 40}...{commit}",
    )
    assert value["source"] == {
        "repository": "joshyorko/codex-action-server",
        "revision": commit,
        "merged": True,
        "commit_url": f"https://github.com/joshyorko/codex-action-server/commit/{commit}",
    }
    assert value["publication"] == {
        "image_repository": "ghcr.io/joshyorko/codex-action-server",
        "image_tag": f"sha-{commit}",
        "digest": digest,
        "revision": commit,
        "status": "published",
        "run_url": "https://github.com/joshyorko/codex-action-server/actions/runs/125",
        "platform": "linux/amd64",
    }
    assert value["qualification"]["status"] == "qualified"
    assert value["qualification"]["native_release"] == {
        "version": "0.166.0",
        "tag": "rust-v0.166.0",
        "asset_url": "https://github.com/openai/codex/releases/download/rust-v0.166.0/codex-x86_64-unknown-linux-musl.tar.gz",
        "asset_id": "42",
        "asset_sha256": "a" * 64,
        "binary_sha256": "b" * 64,
        "fixture_path": "tests/fixtures/protocol/codex_0.166.0_native_methods.json",
        "fixture_sha256": receipt._sha256(
            tmp_path / "tests/fixtures/protocol/codex_0.166.0_native_methods.json"
        ),
        "qualification_run_url": "https://github.com/joshyorko/codex-action-server/actions/runs/123",
    }
    assert value["qualification"]["method_verdicts"] == {"app/read": "already_approved"}
    assert value["qualification"]["behavior_coverage"] == {
        "app/read": {"result": "passed", "strategy": "isolated_fixture"}
    }
    assert value["qualification"]["withheld_methods"] == []
    assert value["receipt_sha256"] == receipt._canonical_receipt_digest(value)
    expected_surface_hash = hashlib.sha256(
        b'[{"name":"app/read","owner":"typed_action"}]'
    ).hexdigest()
    assert value["qualification"]["surface"]["sha256"] == expected_surface_hash

    import jsonschema

    schema = json.loads(
        (ROOT / ".github/schemas/cas-qualified-release-v1.schema.json").read_text()
    )
    jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    ).validate(value)


def test_receipt_refuses_an_image_built_from_another_commit(tmp_path):
    _write_receipt_inputs(tmp_path)
    commit = "e" * 40
    try:
        receipt.build_receipt(
            tmp_path,
            repository="joshyorko/codex-action-server",
            source_commit=commit,
            image="ghcr.io/joshyorko/codex-action-server",
            image_digest="sha256:" + "f" * 64,
            image_revision="1" * 40,
            qualification_run_url="https://github.com/joshyorko/codex-action-server/actions/runs/123",
            required_ci_url="https://github.com/joshyorko/codex-action-server/actions/runs/124",
            publication_run_url="https://github.com/joshyorko/codex-action-server/actions/runs/125",
            comparison_url=f"https://github.com/joshyorko/codex-action-server/compare/{'d' * 40}...{commit}",
        )
    except ValueError as error:
        assert "image revision" in str(error)
    else:
        raise AssertionError("receipt accepted a mismatched image revision")


def test_partial_method_surface_never_emits_a_qualified_handoff(tmp_path):
    _write_receipt_inputs(tmp_path)
    fixture_path = (
        tmp_path / "tests/fixtures/protocol/codex_0.166.0_native_methods.json"
    )
    fixture = json.loads(fixture_path.read_text())
    fixture["methods"][0]["status"] = "needs_review"
    fixture_path.write_text(json.dumps(fixture))
    commit = "e" * 40

    value = receipt.build_receipt(
        tmp_path,
        repository="joshyorko/codex-action-server",
        source_commit=commit,
        image="ghcr.io/joshyorko/codex-action-server",
        image_digest="sha256:" + "f" * 64,
        image_revision=commit,
        qualification_run_url="https://github.com/joshyorko/codex-action-server/actions/runs/123",
        required_ci_url="https://github.com/joshyorko/codex-action-server/actions/runs/124",
        publication_run_url="https://github.com/joshyorko/codex-action-server/actions/runs/125",
        comparison_url=f"https://github.com/joshyorko/codex-action-server/compare/{'d' * 40}...{commit}",
    )

    assert value is None


def test_experimental_method_without_exact_runtime_approval_blocks_handoff(tmp_path):
    _write_receipt_inputs(tmp_path)
    manifest_path = tmp_path / "ci/codex_qualification/approved_methods.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["approvals"] = []
    manifest_path.write_text(json.dumps(manifest))
    commit = "e" * 40

    value = receipt.build_receipt(
        tmp_path,
        repository="joshyorko/codex-action-server",
        source_commit=commit,
        image="ghcr.io/joshyorko/codex-action-server",
        image_digest="sha256:" + "f" * 64,
        image_revision=commit,
        qualification_run_url="https://github.com/joshyorko/codex-action-server/actions/runs/123",
        required_ci_url="https://github.com/joshyorko/codex-action-server/actions/runs/124",
        publication_run_url="https://github.com/joshyorko/codex-action-server/actions/runs/125",
        comparison_url=f"https://github.com/joshyorko/codex-action-server/compare/{'d' * 40}...{commit}",
    )

    assert value is None


def test_native_qualification_run_must_match_merged_fixture_provenance(tmp_path):
    _write_receipt_inputs(tmp_path)
    manifest_path = tmp_path / "ci/codex_qualification/approved_methods.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["sources"]["0.166.0"]["native_methods_qualification_run"] = (
        "https://github.com/joshyorko/codex-action-server/actions/runs/999"
    )
    manifest_path.write_text(json.dumps(manifest))

    try:
        receipt.build_receipt(
            tmp_path,
            repository="joshyorko/codex-action-server",
            source_commit="e" * 40,
            image="ghcr.io/joshyorko/codex-action-server",
            image_digest="sha256:" + "f" * 64,
            image_revision="e" * 40,
            qualification_run_url="https://github.com/joshyorko/codex-action-server/actions/runs/123",
            required_ci_url="https://github.com/joshyorko/codex-action-server/actions/runs/124",
            publication_run_url="https://github.com/joshyorko/codex-action-server/actions/runs/125",
            comparison_url=f"https://github.com/joshyorko/codex-action-server/compare/{'d' * 40}...{'e' * 40}",
        )
    except ValueError as error:
        assert "qualification run mismatch" in str(error)
    else:
        raise AssertionError(
            "receipt accepted different fixture and manifest run provenance"
        )
