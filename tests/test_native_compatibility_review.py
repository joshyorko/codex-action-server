"""Admission binds reviewed native evidence to one method and current inputs."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "native_compatibility_review", ROOT / "ci/native_compatibility_review.py"
)
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)
VERSION = "0.166.0"
METHOD = "thread/search"
PROBE_REF = "tests/fixtures/protocol/probe.json"
REVIEW_REF = "ci/codex_qualification/reviewed_schema_changes.json"
IDENTITY = {
    "native_version": VERSION,
    "binary_sha256": "a" * 64,
    "source_tag": "rust-v0.166.0",
    "source_url": (
        "https://github.com/openai/codex/releases/download/rust-v0.166.0/"
        "codex-x86_64-unknown-linux-musl.tar.gz"
    ),
    "source_asset_sha256": "b" * 64,
    "source_asset_id": "42",
}
REQUEST = {"type": "object", "properties": {"query": {"type": "string"}}}
RESPONSE = {"type": "object", "properties": {"data": {"type": "array"}}}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def write_json(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def probe(tmp_path):
    fixture = {
        **IDENTITY,
        "typed_regression_tests": "passed",
        "methods": {
            METHOD: {
                "request_schema": copy.deepcopy(REQUEST),
                "response_schema": copy.deepcopy(RESPONSE),
                "protocol_smoke": {
                    "status": "passed",
                    "positive": True,
                    "required_features": {"step_model_switching": True},
                    "effects": {"result_observed": True},
                },
            }
        },
    }
    manifest = {
        "schema_version": 1,
        "sources": {
            VERSION: {
                **{
                    key: value
                    for key, value in IDENTITY.items()
                    if key != "native_version"
                },
                "kind": "native_methods_fixture",
                "fixture": PROBE_REF,
            }
        },
        "approvals": [
            {
                "version": VERSION,
                "method": METHOD,
                "provenance_ref": VERSION,
                "schema_fingerprint": {
                    "request_sha256": digest(REQUEST),
                    "response_sha256": digest(RESPONSE),
                },
            }
        ],
    }
    write_json(tmp_path, PROBE_REF, fixture)
    return tmp_path, manifest, fixture


def admit_probe(root, manifest, **changes):
    args = dict(
        root=root,
        manifest=manifest,
        method=METHOD,
        version=VERSION,
        binary_sha256=IDENTITY["binary_sha256"],
        asset_sha256=IDENTITY["source_asset_sha256"],
        asset_id=IDENTITY["source_asset_id"],
        request_schema=REQUEST,
        response_schema=RESPONSE,
    )
    return review.exact_reviewed_probe(**(args | changes))


def test_admission_preserves_positive_probe_effects_and_feature_requirements(probe):
    root, manifest, _ = probe
    value = admit_probe(root, manifest)
    assert value is not None
    assert value["kind"] == "reviewed_exact_version_probe"
    assert value["fixture"] == PROBE_REF
    assert (
        value["fixture_sha256"]
        == hashlib.sha256((root / PROBE_REF).read_bytes()).hexdigest()
    )
    assert value["required_features"] == {"step_model_switching": True}
    assert value["effects"] == {"result_observed": True}


@pytest.mark.parametrize(
    "changes",
    [
        {"method": "thread/other"},
        {"version": "0.167.0"},
        {"binary_sha256": "c" * 64},
        {"asset_sha256": "c" * 64},
        {"asset_id": "43"},
        {"request_schema": {"type": "object"}},
        {"response_schema": {"type": "object"}},
    ],
)
def test_probe_never_transfers_admission_to_changed_candidate_inputs(probe, changes):
    root, manifest, _ = probe
    assert admit_probe(root, manifest, **changes) is None


@pytest.mark.parametrize("positive", [False, 1, "true", None])
def test_probe_requires_boolean_positive_native_evidence(probe, positive):
    root, manifest, fixture = probe
    fixture["methods"][METHOD]["protocol_smoke"]["positive"] = positive
    write_json(root, PROBE_REF, fixture)
    assert admit_probe(root, manifest) is None


@pytest.mark.parametrize(
    "location, key, value",
    [
        ("fixture", "native_version", "0.167.0"),
        ("fixture", "binary_sha256", "c" * 64),
        ("fixture", "source_tag", "rust-v0.167.0"),
        ("fixture", "source_url", "https://example.invalid/archive"),
        ("fixture", "source_asset_id", "43"),
        ("fixture", "source_asset_sha256", "c" * 64),
        ("fixture", "typed_regression_tests", "failed"),
        ("source", "kind", "compatibility_fixture"),
        ("source", "binary_sha256", "c" * 64),
        ("source", "source_tag", "rust-v0.167.0"),
        ("source", "source_asset_id", "43"),
        ("approval", "provenance_ref", "0.167.0"),
        ("approval", "schema_fingerprint", {}),
    ],
)
def test_probe_rejects_changed_reviewed_provenance(probe, location, key, value):
    root, manifest, fixture = probe
    target = {
        "fixture": fixture,
        "source": manifest["sources"][VERSION],
        "approval": manifest["approvals"][0],
    }[location]
    target[key] = value
    write_json(root, PROBE_REF, fixture)
    assert admit_probe(root, manifest) is None


def test_probe_rejects_missing_duplicate_approvals_and_failed_smoke(probe):
    root, manifest, fixture = probe
    approval = manifest["approvals"][0]
    manifest["approvals"] = []
    assert admit_probe(root, manifest) is None
    manifest["approvals"] = [approval, copy.deepcopy(approval)]
    assert admit_probe(root, manifest) is None
    manifest["approvals"] = [approval]
    fixture["methods"][METHOD]["protocol_smoke"]["status"] = "failed"
    write_json(root, PROBE_REF, fixture)
    assert admit_probe(root, manifest) is None


def test_all_thirteen_previously_withheld_methods_reuse_exact_reviewed_probes():
    manifest = json.loads(
        (ROOT / "ci/codex_qualification/approved_methods.json").read_text()
    )
    source = manifest["sources"][VERSION]
    fixture = json.loads((ROOT / source["fixture"]).read_text())
    methods = set(fixture["methods"]) - {"app/read", "server/diagnostics"}
    assert len(methods) == 13
    for method in methods:
        candidate = json.loads(json.dumps(fixture["methods"][method]))
        assert (
            admit_probe(
                ROOT,
                manifest,
                method=method,
                binary_sha256=source["binary_sha256"],
                asset_sha256=source["source_asset_sha256"],
                asset_id=source["source_asset_id"],
                request_schema=candidate["request_schema"],
                response_schema=candidate["response_schema"],
            )
            is not None
        ), method


@pytest.fixture
def semantic_review(tmp_path):
    source = tmp_path / "src/reader.py"
    source.parent.mkdir()
    source.write_text("def read(value):\n    return value['data']\n")
    test = tmp_path / "tests/test_reader.py"
    test.parent.mkdir(exist_ok=True)
    test.write_text("def test_read():\n    assert True\n")
    row = {
        "schema_fingerprint": {"request_sha256": "c" * 64, "response_sha256": "d" * 64},
        "source_files_sha256": {
            "src/reader.py": hashlib.sha256(source.read_bytes()).hexdigest()
        },
        "tests": ["tests/test_reader.py::test_read"],
        "summary": "The reader consumes data and accepts the reviewed optional field.",
        "request_fields": [],
        "response_fields": ["data"],
    }
    document = {
        "schema_version": 1,
        "versions": {VERSION: {**IDENTITY, "methods": {METHOD: row}}},
    }
    write_json(tmp_path, REVIEW_REF, document)
    return tmp_path, document, row


def admit_review(root, row, **changes):
    args = dict(
        root=root,
        method=METHOD,
        version=VERSION,
        binary_sha256=IDENTITY["binary_sha256"],
        asset_sha256=IDENTITY["source_asset_sha256"],
        asset_id=IDENTITY["source_asset_id"],
        schema_fingerprint=row["schema_fingerprint"],
        regression_tests=["tests/test_reader.py"],
    )
    return review.reviewed_schema_change(**(args | changes))


def test_consumed_contract_admission_binds_review_to_current_code_and_mapped_tests(
    semantic_review,
):
    root, _, row = semantic_review
    value = admit_review(root, row)
    assert value is not None
    assert value["kind"] == "reviewed_consumed_contract"
    assert value["review_file"] == REVIEW_REF
    assert value["tests"] == ["tests/test_reader.py::test_read"]
    assert value["response_fields"] == ["data"]
    (root / "src/reader.py").write_text("def read(value):\n    return value['other']\n")
    assert admit_review(root, row) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"method": "thread/other"},
        {"version": "0.167.0"},
        {"binary_sha256": "e" * 64},
        {"asset_sha256": "e" * 64},
        {"asset_id": "43"},
        {
            "schema_fingerprint": {
                "request_sha256": "e" * 64,
                "response_sha256": "d" * 64,
            }
        },
        {"regression_tests": ["tests/test_other.py"]},
    ],
)
def test_consumed_review_never_transfers_admission_to_other_inputs(
    semantic_review, changes
):
    root, _, row = semantic_review
    assert admit_review(root, row, **changes) is None


@pytest.mark.parametrize(
    "key, value",
    [
        ("source_files_sha256", {}),
        ("source_files_sha256", {"src/missing.py": "a" * 64}),
        ("tests", []),
        ("tests", ["tests/test_reader.py"]),
        ("tests", ["tests/missing.py::test_read"]),
        ("summary", ""),
        ("request_fields", [""]),
        ("response_fields", "data"),
        ("response_fields", ["data", "data"]),
    ],
)
def test_incomplete_consumed_contract_review_does_not_admit(
    semantic_review, key, value
):
    root, document, row = semantic_review
    row[key] = value
    write_json(root, REVIEW_REF, document)
    assert admit_review(root, row) is None


@pytest.mark.parametrize("key", list(IDENTITY))
def test_consumed_review_rejects_changed_release_provenance(semantic_review, key):
    root, document, row = semantic_review
    document["versions"][VERSION][key] = "changed"
    write_json(root, REVIEW_REF, document)
    assert admit_review(root, row) is None


def test_consumed_review_rejects_a_removed_mapped_test(semantic_review):
    root, _, row = semantic_review
    (root / "tests/test_reader.py").unlink()
    assert admit_review(root, row) is None


@pytest.mark.parametrize(
    "source, node",
    [
        ("def test_renamed():\n    assert True\n", "test_read"),
        ("test_read = True\n", "test_read"),
        ("def read():\n    return True\n", "read"),
        ("class TestReader:\n    pass\n", "TestReader"),
        ("class TestReader:\n    pass\n", "TestReader::test_read"),
        (
            "class TestRenamed:\n    def test_read(self):\n        assert True\n",
            "TestReader::test_read",
        ),
        (
            "class Reader:\n    def test_read(self):\n        assert True\n",
            "Reader::test_read",
        ),
        (
            "def TestReader():\n    def test_read():\n        assert True\n",
            "TestReader::test_read",
        ),
        ("def test_read(:\n    assert True\n", "test_read"),
    ],
)
def test_consumed_review_rejects_missing_or_non_test_nodes(
    semantic_review, source, node
):
    root, document, row = semantic_review
    (root / "tests/test_reader.py").write_text(source)
    row["tests"] = [f"tests/test_reader.py::{node}"]
    write_json(root, REVIEW_REF, document)
    assert admit_review(root, row) is None


@pytest.mark.parametrize(
    "source, node",
    [
        ("def test_read():\n    assert True\n", "test_read[example]"),
        (
            "class TestReader:\n    def test_read(self):\n        assert True\n",
            "TestReader::test_read",
        ),
        (
            "import unittest\nclass ReaderTests(unittest.TestCase):\n"
            "    def test_read(self):\n        assert True\n",
            "ReaderTests::test_read",
        ),
    ],
)
def test_consumed_review_accepts_existing_pytest_and_unittest_nodes(
    semantic_review, source, node
):
    root, document, row = semantic_review
    (root / "tests/test_reader.py").write_text(source)
    row["tests"] = [f"tests/test_reader.py::{node}"]
    write_json(root, REVIEW_REF, document)
    assert admit_review(root, row) is not None


@pytest.mark.parametrize("version,expected_count", [("0.166.0", 22), ("0.167.0", 19)])
def test_all_current_consumed_contract_reviews_have_existing_test_nodes(
    version, expected_count
):
    document = json.loads((ROOT / REVIEW_REF).read_text())
    catalog = json.loads(
        (ROOT / "ci/codex_qualification/native_methods.json").read_text()
    )
    methods = {row["method"]: row for row in catalog["methods"]}
    release = document["versions"][version]
    assert len(release["methods"]) == expected_count
    for method, row in release["methods"].items():
        assert (
            review.reviewed_schema_change(
                ROOT,
                method,
                version,
                release["binary_sha256"],
                release["source_asset_sha256"],
                release["source_asset_id"],
                row["schema_fingerprint"],
                methods[method]["regression_tests"],
            )
            is not None
        ), method


@pytest.mark.parametrize("kind", ["probe", "source", "test"])
def test_review_evidence_paths_cannot_escape_source_tree(probe, semantic_review, kind):
    root, manifest, _ = probe
    if kind == "probe":
        manifest["sources"][VERSION]["fixture"] = "../outside.json"

        def call():
            return admit_probe(root, manifest)

    else:
        root, document, row = semantic_review
        if kind == "source":
            row["source_files_sha256"] = {"../outside.py": "a" * 64}
        else:
            row["tests"] = ["../outside.py::test_read"]
        write_json(root, REVIEW_REF, document)

        def call():
            return admit_review(root, row)

    try:
        value = call()
    except ValueError:
        return
    assert value is None


def test_probe_rejects_a_fixture_symlink_outside_the_source_tree(probe, tmp_path):
    root, manifest, fixture = probe
    inside = root / "inside"
    inside.mkdir()
    outside = root / PROBE_REF
    (inside / "probe.json").symlink_to(outside)
    manifest["sources"][VERSION]["fixture"] = "probe.json"
    with pytest.raises(ValueError, match="inside the source tree"):
        admit_probe(inside, manifest)


def test_missing_or_invalid_review_document_never_admits(tmp_path):
    row = {
        "schema_fingerprint": {"request_sha256": "c" * 64, "response_sha256": "d" * 64}
    }
    assert admit_review(tmp_path, row) is None
    path = tmp_path / REVIEW_REF
    path.parent.mkdir(parents=True)
    path.write_text("not json")
    assert admit_review(tmp_path, row) is None
