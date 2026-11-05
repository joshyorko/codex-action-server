"""Exact-version approvals require method-specific positive live evidence."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "method_qualification_generator", ROOT / "ci/generate_native_qualification.py"
)
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    source = {
        "kind": "native_methods_fixture",
        "fixture": "native-methods.json",
        "binary_sha256": "a" * 64,
        "source_tag": "rust-v0.166.0",
        "source_url": "https://example.invalid/codex-0.166.0.tar.gz",
        "source_asset_sha256": "b" * 64,
        "source_asset_id": "fixture-asset",
        "qualification_run": "https://example.invalid/qualification/1",
    }
    request = {
        "type": "object",
        "required": ["searchTerm"],
        "properties": {"searchTerm": {"type": "string"}},
    }
    response = {
        "type": "object",
        "required": ["data"],
        "properties": {"data": {"type": "array"}},
    }
    fixture = {
        "native_version": "0.166.0",
        **{
            key: value
            for key, value in source.items()
            if key not in ("kind", "fixture")
        },
        "typed_regression_tests": "passed",
        "methods": {
            "thread/search": {
                "request_schema": request,
                "response_schema": response,
                "protocol_smoke": {"status": "passed", "positive": True},
            }
        },
    }
    document = {
        "schema_version": 1,
        "sources": {"0.166.0": source},
        "approvals": [
            {
                "version": "0.166.0",
                "method": "thread/search",
                "provenance_ref": "0.166.0",
                "schema_fingerprint": {
                    "request_sha256": hashlib.sha256(
                        b'{"properties":{"searchTerm":{"type":"string"}},"required":["searchTerm"],"type":"object"}'
                    ).hexdigest(),
                    "response_sha256": hashlib.sha256(
                        b'{"properties":{"data":{"type":"array"}},"required":["data"],"type":"object"}'
                    ).hexdigest(),
                },
            }
        ],
    }
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    monkeypatch.setattr(generator, "MANIFEST", tmp_path / "manifest.json")

    def load():
        (tmp_path / "native-methods.json").write_text(json.dumps(fixture))
        generator.MANIFEST.write_text(json.dumps(document))
        return generator.load_approvals()

    return source, fixture, document, load


def test_native_method_fixture_admits_only_qualified_exact_method(manifest):
    _, _, _, load = manifest
    assert load() == {"0.166.0": {"thread/search"}}


@pytest.mark.parametrize(
    "key",
    [
        "native_version",
        "binary_sha256",
        "source_tag",
        "source_url",
        "source_asset_sha256",
        "source_asset_id",
        "qualification_run",
    ],
)
def test_native_method_fixture_rejects_changed_provenance(manifest, key):
    _, fixture, _, load = manifest
    fixture[key] = "changed"
    with pytest.raises(ValueError, match="provenance mismatch"):
        load()


@pytest.mark.parametrize(
    "key",
    [
        "binary_sha256",
        "source_tag",
        "source_url",
        "source_asset_sha256",
        "source_asset_id",
    ],
)
def test_native_method_fixture_requires_source_provenance(manifest, key):
    source, fixture, _, load = manifest
    del source[key]
    del fixture[key]
    with pytest.raises(ValueError, match="provenance mismatch"):
        load()


@pytest.mark.parametrize("status", [None, "not_run", "failed"])
def test_native_method_fixture_requires_typed_regressions(manifest, status):
    _, fixture, _, load = manifest
    fixture["typed_regression_tests"] = status
    with pytest.raises(ValueError, match="typed regressions"):
        load()


@pytest.mark.parametrize(
    "smoke",
    [
        None,
        {},
        {"status": "failed", "positive": True},
        {"status": "passed", "positive": False},
        {"status": "passed", "positive": 1},
    ],
)
def test_native_method_fixture_requires_positive_live_method_evidence(manifest, smoke):
    _, fixture, _, load = manifest
    fixture["methods"]["thread/search"]["protocol_smoke"] = smoke
    with pytest.raises(ValueError, match="positive live"):
        load()


@pytest.mark.parametrize("direction", ["request_schema", "response_schema"])
def test_native_method_fixture_rejects_changed_schema_fingerprint(manifest, direction):
    _, fixture, _, load = manifest
    fixture["methods"]["thread/search"][direction]["required"] = []
    with pytest.raises(ValueError, match="schema fingerprint mismatch"):
        load()


def test_native_method_fixture_cannot_borrow_another_methods_evidence(manifest):
    _, _, document, load = manifest
    document["approvals"][0]["method"] = "thread/searchOccurrences"
    with pytest.raises(ValueError, match="method qualification missing"):
        load()


@pytest.mark.parametrize("direction", ["request_schema", "response_schema"])
def test_native_method_fixture_requires_schema_objects(manifest, direction):
    _, fixture, _, load = manifest
    fixture["methods"]["thread/search"][direction] = []
    with pytest.raises(ValueError, match="schema objects"):
        load()
