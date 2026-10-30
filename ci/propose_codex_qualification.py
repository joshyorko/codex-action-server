#!/usr/bin/env python3
"""Prepare a reviewed qualification-PR patch from a passing CI artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ci"))
from generate_native_qualification import render_module
from qualify_codex_schema import normalize

METHOD = "server/diagnostics"
VERSION_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
SCHEMA_NAMES = ("ServerDiagnosticsParams", "ServerDiagnosticsResponse")
MAX_SCHEMA_BYTES = 2 * 1024 * 1024


def _read_json(path: Path):
    if path.stat().st_size > MAX_SCHEMA_BYTES:
        raise ValueError(f"qualification artifact is too large: {path.name}")
    return json.loads(path.read_text())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _render_docs(document: str, provenance: dict, fingerprints: dict, run_url: str) -> str:
    version = provenance["version"]
    marker = f"<!-- codex-qualification:{version} -->"
    if marker in document:
        return document
    section = "\n".join(
        (
            marker,
            f"## Qualified candidate: Codex {version}",
            "",
            f"- Method: `{METHOD}`; request schema SHA-256: `{fingerprints['request_sha256']}`.",
            f"- Response schema SHA-256: `{fingerprints['response_sha256']}`.",
            f"- Source: [`{provenance['source_tag']}`]({provenance['source_url']}); asset SHA-256: `{provenance['source_asset_sha256']}`.",
            f"- Binary SHA-256: `{provenance['binary_sha256']}`.",
            f"- Live protocol smoke and typed regressions passed in [qualification run]({run_url}).",
            "- Admission is version- and method-specific; this PR does not authorize other experimental methods or update installed daemons.",
        )
    )
    return document.rstrip() + "\n\n" + section + "\n"


def prepare_proposal(artifact: Path, output: Path, run_url: str) -> dict:
    provenance = _read_json(artifact / "provenance.json")
    diff = _read_json(artifact / "server-diagnostics-schema-diff.json")
    smoke = _read_json(artifact / "protocol-smoke.json")
    release = _read_json(artifact / "source-release.json")
    version = provenance.get("version")
    if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError("candidate version is not a stable semantic version")
    if provenance.get("qualification") != "compatible_pending_review":
        raise ValueError("candidate did not pass schema and protocol qualification")
    if provenance.get("typed_regression_tests") != "passed":
        raise ValueError("typed native regressions did not pass")
    if provenance.get("live_protocol_smoke") != "passed" or smoke.get("status") != "passed":
        raise ValueError("live app-server protocol smoke did not pass")
    if diff.get("identical") is not True:
        raise ValueError("candidate diagnostics schema differs from the reviewed contract")
    asset = release.get("asset", {})
    if (
        release.get("tag_name") != provenance.get("source_tag")
        or asset.get("browser_download_url") != provenance.get("source_url")
        or asset.get("digest", "").removeprefix("sha256:")
        != provenance.get("source_asset_sha256")
        or str(asset.get("id")) != provenance.get("source_asset_id")
    ):
        raise ValueError("candidate source-release provenance does not match qualification")

    candidate_dir = artifact / "candidate-schemas"
    generated = {}
    for name in SCHEMA_NAMES:
        path = candidate_dir / f"{name}.json"
        schema = _read_json(path)
        if schema.get("title") != name:
            raise ValueError(f"candidate schema title mismatch: {name}")
        generated[name] = schema
    baseline = _read_json(
        ROOT / "tests/fixtures/protocol/codex_0.162.1_server_diagnostics.json"
    )
    normalized = {name: normalize(generated[name]) for name in SCHEMA_NAMES}
    if normalized != {
        name: normalize(baseline["schemas"][name]) for name in SCHEMA_NAMES
    }:
        raise ValueError("candidate schema no longer matches the reviewed baseline")

    generated_hashes = {
        Path(path).name: digest
        for path, digest in provenance.get("generated_files_sha256", {}).items()
    }
    fingerprints = {
        "request_sha256": generated_hashes.get("ServerDiagnosticsParams.json"),
        "response_sha256": generated_hashes.get("ServerDiagnosticsResponse.json"),
    }
    if any(not isinstance(value, str) or len(value) != 64 for value in fingerprints.values()):
        raise ValueError("candidate schema fingerprints are incomplete")
    for name in SCHEMA_NAMES:
        if _sha256(candidate_dir / f"{name}.json") != generated_hashes.get(f"{name}.json"):
            raise ValueError(f"candidate schema hash mismatch: {name}")

    manifest_path = ROOT / "ci/codex_qualification/approved_methods.json"
    manifest = _read_json(manifest_path)
    if version in manifest["sources"]:
        existing = [
            item
            for item in manifest["approvals"]
            if item.get("version") == version and item.get("method") == METHOD
        ]
        if existing:
            output.mkdir(parents=True, exist_ok=True)
            result = {"status": "already-approved", "version": version, "files": []}
            _write_json(output / "proposal.json", result)
            return result
        raise ValueError("candidate version already has a different provenance record")

    fixture_relative = f"tests/fixtures/protocol/codex_{version}_server_diagnostics.json"
    fixture = {
        "binary_sha256": provenance["binary_sha256"],
        "generated_files_sha256": generated_hashes,
        "generation_command": "codex app-server generate-json-schema --experimental --out <private-temporary-directory>",
        "live_protocol_smoke": smoke,
        "method": METHOD,
        "native_version": version,
        "qualification_run": run_url,
        "request_variant": baseline["request_variant"],
        "schemas": generated,
        "source_asset_sha256": provenance["source_asset_sha256"],
        "source_asset_id": provenance["source_asset_id"],
        "source_tag": provenance["source_tag"],
        "source_url": provenance["source_url"],
        "typed_regression_tests": "passed",
    }
    manifest["sources"][version] = {
        "binary_sha256": provenance["binary_sha256"],
        "fixture": fixture_relative,
        "kind": "diagnostics_fixture",
        "qualification_run": run_url,
        "source_asset_sha256": provenance["source_asset_sha256"],
        "source_asset_id": provenance["source_asset_id"],
        "source_tag": provenance["source_tag"],
        "source_url": provenance["source_url"],
    }
    manifest["approvals"].append(
        {
            "method": METHOD,
            "provenance_ref": version,
            "schema_fingerprint": fingerprints,
            "version": version,
        }
    )
    methods_by_version: dict[str, set[str]] = {}
    for approval in manifest["approvals"]:
        methods_by_version.setdefault(approval["version"], set()).add(
            approval["method"]
        )
    generated_module = render_module(methods_by_version)

    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / fixture_relative, fixture)
    _write_json(output / "ci/codex_qualification/approved_methods.json", manifest)
    module_path = output / "src/codex_shared/native_qualification.py"
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(generated_module)
    docs_path = ROOT / "docs/acceptance/codex-native-qualification.md"
    docs = _render_docs(docs_path.read_text(), provenance, fingerprints, run_url)
    (output / "docs/acceptance/codex-native-qualification.md").parent.mkdir(
        parents=True, exist_ok=True
    )
    (output / "docs/acceptance/codex-native-qualification.md").write_text(docs)
    files = [
        fixture_relative,
        "ci/codex_qualification/approved_methods.json",
        "src/codex_shared/native_qualification.py",
        "docs/acceptance/codex-native-qualification.md",
    ]
    _write_json(output / "proposal.json", {"status": "prepared", "version": version, "files": files})
    return {"status": "prepared", "version": version, "files": files}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args()
    result = prepare_proposal(args.artifact, args.output, args.run_url)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
