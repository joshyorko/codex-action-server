#!/usr/bin/env python3
"""Validate reviewed native-schema approvals and generate the runtime gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "ci/codex_qualification/approved_methods.json"
DESTINATION = ROOT / "src/codex_shared/native_qualification.py"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _fixture_path(source: dict[str, object]) -> Path:
    path = (ROOT / str(source["fixture"])).resolve()
    if ROOT not in path.parents:
        raise ValueError("approval fixture must stay inside the repository")
    return path


def load_approvals() -> dict[str, set[str]]:
    manifest = json.loads(MANIFEST.read_text())
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported native-qualification manifest version")
    sources = manifest.get("sources")
    approvals = manifest.get("approvals")
    if not isinstance(sources, dict) or not isinstance(approvals, list):
        raise ValueError("native-qualification manifest is malformed")

    fixture_cache = {}
    methods_by_version: dict[str, set[str]] = {}
    seen = set()
    for approval in approvals:
        version = approval.get("version")
        method = approval.get("method")
        provenance_ref = approval.get("provenance_ref")
        fingerprint = approval.get("schema_fingerprint")
        if not all(isinstance(value, str) and value for value in (version, method)):
            raise ValueError("approval must name an exact version and method")
        if provenance_ref != version or version not in sources:
            raise ValueError(f"approval {version}/{method} has no version provenance")
        key = (version, method)
        if key in seen:
            raise ValueError(f"duplicate native-method approval: {version}/{method}")
        seen.add(key)
        if not isinstance(fingerprint, dict):
            raise ValueError(f"approval {version}/{method} lacks a schema fingerprint")
        request_hash = fingerprint.get("request_sha256")
        response_hash = fingerprint.get("response_sha256")
        if not all(
            isinstance(value, str) and _SHA256.fullmatch(value)
            for value in (request_hash, response_hash)
        ):
            raise ValueError(f"approval {version}/{method} has an invalid fingerprint")

        source = sources[version]
        fixture = str(_fixture_path(source))
        if fixture not in fixture_cache:
            fixture_cache[fixture] = json.loads(Path(fixture).read_text())
        source_data = fixture_cache[fixture]
        method_evidence_ref = approval.get("method_evidence_ref")
        if method_evidence_ref is not None:
            expected_ref = source.get("native_methods_fixture")
            if expected_ref is None and source.get("kind") == "native_methods_fixture":
                expected_ref = source.get("fixture")
            if method_evidence_ref != expected_ref or not isinstance(
                method_evidence_ref, str
            ):
                raise ValueError(
                    f"method evidence reference mismatch for {version}/{method}"
                )
            evidence_path = (ROOT / method_evidence_ref).resolve()
            if ROOT not in evidence_path.parents:
                raise ValueError(
                    "method evidence fixture must stay inside the repository"
                )
            evidence_fixture = json.loads(evidence_path.read_text())
            if evidence_fixture.get(
                "native_version"
            ) != version or evidence_fixture.get("binary_sha256") != source.get(
                "binary_sha256"
            ):
                raise ValueError(
                    f"method fixture binary provenance mismatch for {version}"
                )
            for key in (
                "source_tag",
                "source_url",
                "source_asset_sha256",
                "source_asset_id",
            ):
                if key in source and evidence_fixture.get(key) != source[key]:
                    raise ValueError(f"{key} provenance mismatch for Codex {version}")
            evidence_run = source.get(
                "native_methods_qualification_run", source.get("qualification_run")
            )
            if evidence_fixture.get("qualification_run") != evidence_run:
                raise ValueError(
                    f"qualification_run provenance mismatch for Codex {version}"
                )
            method_row = next(
                (
                    item
                    for item in evidence_fixture.get("methods", [])
                    if item.get("method") == method
                ),
                None,
            )
            if (
                not isinstance(method_row, dict)
                or method_row.get("status") != "compatible_pending_review"
            ):
                raise ValueError(
                    f"method evidence is not qualified for {version}/{method}"
                )
            expected = (
                method_row.get("schema_fingerprint", {}).get("request_sha256"),
                method_row.get("schema_fingerprint", {}).get("response_sha256"),
            )
        elif source.get("kind") == "compatibility_fixture":
            if source_data.get("source_commits", {}).get(version) != source.get(
                "source_commit"
            ):
                raise ValueError(f"source commit mismatch for Codex {version}")
            contract = next(
                (
                    item
                    for item in source_data.get("cas_experimental_contracts", [])
                    if item.get("method") == method
                ),
                None,
            )
            expected = (
                (
                    contract.get("request_sha256", {}).get(version),
                    contract.get("response_sha256", {}).get(version),
                )
                if contract
                else None
            )
        elif source.get("kind") == "diagnostics_fixture":
            if source_data.get("native_version") != version or source_data.get(
                "binary_sha256"
            ) != source.get("binary_sha256"):
                raise ValueError(f"binary provenance mismatch for Codex {version}")
            for key in (
                "source_tag",
                "source_url",
                "source_asset_sha256",
                "source_asset_id",
                "qualification_run",
            ):
                if key in source and source_data.get(key) != source[key]:
                    raise ValueError(f"{key} provenance mismatch for Codex {version}")
            if method != "server/diagnostics":
                raise ValueError(f"unexpected method in diagnostics fixture: {method}")
            hashes = source_data.get("generated_files_sha256", {})
            expected = (
                hashes.get("ServerDiagnosticsParams.json"),
                hashes.get("ServerDiagnosticsResponse.json"),
            )
        else:
            raise ValueError(f"unknown provenance kind for Codex {version}")
        if expected != (request_hash, response_hash):
            raise ValueError(f"schema fingerprint mismatch for {version}/{method}")
        methods_by_version.setdefault(version, set()).add(method)

    unreferenced = set(sources) - set(methods_by_version)
    for version in unreferenced:
        source = sources[version]
        if source.get("kind") != "native_methods_fixture":
            raise ValueError("manifest provenance contains an unreferenced version")
        fixture = str(_fixture_path(source))
        if fixture not in fixture_cache:
            fixture_cache[fixture] = json.loads(Path(fixture).read_text())
        evidence = fixture_cache[fixture]
        methods = evidence.get("methods")
        if (
            evidence.get("native_version") != version
            or evidence.get("binary_sha256") != source.get("binary_sha256")
            or not isinstance(methods, list)
            or not methods
            or len({row.get("method") for row in methods if isinstance(row, dict)})
            != len(methods)
        ):
            raise ValueError(f"unreferenced method evidence is malformed for {version}")
    return methods_by_version


def render_module(methods_by_version: dict[str, set[str]]) -> str:
    lines = [
        '"""Generated by ci/generate_native_qualification.py; do not edit."""',
        "",
        "REVIEWED_EXPERIMENTAL_METHODS_BY_NATIVE_VERSION = {",
    ]
    for version in sorted(methods_by_version):
        lines.extend((f"    {json.dumps(version)}: frozenset(", "        {"))
        lines.extend(
            f"            {json.dumps(method)},"
            for method in sorted(methods_by_version[version])
        )
        lines.extend(("        }", "    ),"))
    lines.extend(("}", ""))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generated = render_module(load_approvals())
    if args.check:
        if not DESTINATION.is_file() or DESTINATION.read_text() != generated:
            print(
                f"{DESTINATION.relative_to(ROOT)} is stale; regenerate it",
                file=sys.stderr,
            )
            return 1
        return 0
    DESTINATION.write_text(generated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
