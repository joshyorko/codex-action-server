"""Bind existing reviewed native evidence to exact current qualification inputs."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
_REVIEW_FILE = "ci/codex_qualification/reviewed_schema_changes.json"


def native_probe_dependencies_match(root: Path, proof: dict) -> bool:
    if proof.get("fixture") != "ci/probe_operator_native.py":
        return True
    dependencies = proof.get("fixture_dependencies_sha256")
    required = ("ci/qualify_native_methods.py", "ci/qualify_codex_schema.py")
    if not isinstance(dependencies, dict) or set(dependencies) != set(required):
        return False
    try:
        return all(
            dependencies[name]
            == hashlib.sha256(_repo_path(root.resolve(), name).read_bytes()).hexdigest()
            for name in required
        )
    except (OSError, ValueError):
        return False


def _repo_path(root: Path, relative: str) -> Path:
    if (
        not isinstance(relative, str)
        or not relative
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
    ):
        raise ValueError("review evidence path must stay inside the source tree")
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError("review evidence path must stay inside the source tree")
    return path


def _read_document(path: Path) -> tuple[dict, str] | None:
    try:
        if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            return None
        payload = path.read_bytes()
        value = json.loads(payload)
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    return value, hashlib.sha256(payload).hexdigest()


def _release_identity(version, binary_sha256, asset_sha256, asset_id) -> dict | None:
    if (
        not isinstance(version, str)
        or not _VERSION.fullmatch(version)
        or not all(
            isinstance(value, str) and _SHA256.fullmatch(value)
            for value in (binary_sha256, asset_sha256)
        )
        or not isinstance(asset_id, str)
        or not asset_id.isdecimal()
    ):
        return None
    tag = f"rust-v{version}"
    return {
        "native_version": version,
        "binary_sha256": binary_sha256,
        "source_tag": tag,
        "source_url": (
            f"https://github.com/openai/codex/releases/download/{tag}/"
            "codex-x86_64-unknown-linux-musl.tar.gz"
        ),
        "source_asset_sha256": asset_sha256,
        "source_asset_id": asset_id,
    }


def _fingerprint(value) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"request_sha256", "response_sha256"}
        and all(
            isinstance(item, str) and _SHA256.fullmatch(item) for item in value.values()
        )
    )


def _string_list(value, *, nonempty=False) -> bool:
    return (
        isinstance(value, list)
        and (bool(value) or not nonempty)
        and all(isinstance(item, str) and item.strip() for item in value)
        and len(set(value)) == len(value)
    )


def _test_node_exists(path: Path, node: str) -> bool:
    parts = node.split("::")
    parts[-1] = parts[-1].partition("[")[0]
    if not all(parts) or not parts[-1].startswith("test"):
        return False
    try:
        body = ast.parse(path.read_text()).body
    except (OSError, UnicodeError, SyntaxError, ValueError):
        return False
    for name in parts[:-1]:
        parent = next(
            (
                item
                for item in body
                if isinstance(item, ast.ClassDef) and item.name == name
            ),
            None,
        )
        if parent is None or not (
            name.startswith("Test")
            or any(
                isinstance(base, ast.Name)
                and base.id == "TestCase"
                or isinstance(base, ast.Attribute)
                and base.attr == "TestCase"
                for base in parent.bases
            )
        ):
            return False
        body = parent.body
    return any(
        isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == parts[-1]
        for item in body
    )


def exact_reviewed_probe(
    root: Path,
    manifest: dict,
    method: str,
    version: str,
    binary_sha256: str,
    asset_sha256: str,
    asset_id: str,
    request_schema: dict,
    response_schema: dict,
) -> dict | None:
    """Reuse one already approved positive probe for the identical native build."""
    root = root.resolve()
    identity = _release_identity(version, binary_sha256, asset_sha256, asset_id)
    if (
        identity is None
        or not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or not isinstance(manifest.get("sources"), dict)
        or not isinstance(manifest.get("approvals"), list)
        or not isinstance(request_schema, dict)
        or not isinstance(response_schema, dict)
    ):
        return None
    approvals = [
        item
        for item in manifest["approvals"]
        if isinstance(item, dict)
        and item.get("version") == version
        and item.get("method") == method
    ]
    if len(approvals) != 1:
        return None
    approval = approvals[0]
    if approval.get("provenance_ref") != version or not _fingerprint(
        approval.get("schema_fingerprint")
    ):
        return None
    source = manifest["sources"].get(version)
    if (
        not isinstance(source, dict)
        or source.get("kind") != "native_methods_fixture"
        or any(
            source.get(key) != value
            for key, value in identity.items()
            if key != "native_version"
        )
    ):
        return None
    fixture_ref = source.get("fixture")
    fixture_read = _read_document(_repo_path(root, fixture_ref))
    if fixture_read is None:
        return None
    fixture, fixture_hash = fixture_read
    if (
        any(fixture.get(key) != value for key, value in identity.items())
        or fixture.get("typed_regression_tests") != "passed"
        or not isinstance(fixture.get("methods"), dict)
        or (
            "qualification_run" in source
            and fixture.get("qualification_run") != source["qualification_run"]
        )
    ):
        return None
    probe = fixture["methods"].get(method)
    if not isinstance(probe, dict):
        return None
    smoke = probe.get("protocol_smoke")
    if (
        not isinstance(smoke, dict)
        or smoke.get("status") != "passed"
        or smoke.get("positive") is not True
        or not isinstance(smoke.get("effects", {}), dict)
        or not isinstance(smoke.get("required_features", {}), dict)
    ):
        return None
    for kind, schema in (("request", request_schema), ("response", response_schema)):
        if schema != probe.get(f"{kind}_schema"):
            return None
        fingerprint = hashlib.sha256(
            json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if fingerprint != approval["schema_fingerprint"][f"{kind}_sha256"]:
            return None
    return {
        "kind": "reviewed_exact_version_probe",
        "fixture": fixture_ref,
        "fixture_sha256": fixture_hash,
        **identity,
        "method": method,
        "required_features": smoke.get("required_features", {}),
        "effects": smoke.get("effects", {}),
    }


def reviewed_schema_change(
    root: Path,
    method: str,
    version: str,
    binary_sha256: str,
    asset_sha256: str,
    asset_id: str,
    schema_fingerprint: dict,
    regression_tests: list[str],
) -> dict | None:
    """Admit one consumed contract only while its reviewed code and tests match."""
    root = root.resolve()
    identity = _release_identity(version, binary_sha256, asset_sha256, asset_id)
    if identity is None or not _fingerprint(schema_fingerprint):
        return None
    review_read = _read_document(_repo_path(root, _REVIEW_FILE))
    if review_read is None:
        return None
    document, review_hash = review_read
    if document.get("schema_version") != 1 or not isinstance(
        document.get("versions"), dict
    ):
        return None
    release = document["versions"].get(version)
    if (
        not isinstance(release, dict)
        or any(release.get(key) != value for key, value in identity.items())
        or not isinstance(release.get("methods"), dict)
    ):
        return None
    row = release["methods"].get(method)
    if (
        not isinstance(row, dict)
        or row.get("schema_fingerprint") != schema_fingerprint
        or not isinstance(row.get("summary"), str)
        or not row["summary"].strip()
        or not _string_list(row.get("request_fields"))
        or not _string_list(row.get("response_fields"))
        or not _string_list(row.get("tests"), nonempty=True)
        or not _string_list(regression_tests, nonempty=True)
    ):
        return None
    source_files = row.get("source_files_sha256")
    if not isinstance(source_files, dict) or not source_files:
        return None
    for relative, expected_hash in source_files.items():
        path = _repo_path(root, relative)
        if not isinstance(expected_hash, str) or not _SHA256.fullmatch(expected_hash):
            return None
        try:
            if (
                not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash
            ):
                return None
        except OSError:
            return None
    for test in row["tests"]:
        relative, separator, node = test.partition("::")
        path = _repo_path(root, relative)
        if (
            not separator
            or not node.strip()
            or relative not in regression_tests
            or not path.is_file()
            or not _test_node_exists(path, node)
        ):
            return None
    return {
        "kind": "reviewed_consumed_contract",
        "review_file": _REVIEW_FILE,
        "review_file_sha256": review_hash,
        "method": method,
        "summary": row["summary"],
        "source_files_sha256": source_files,
        "tests": row["tests"],
        "request_fields": row["request_fields"],
        "response_fields": row["response_fields"],
    }
