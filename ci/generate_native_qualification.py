#!/usr/bin/env python3
"""Validate reviewed native-schema approvals and generate the runtime gate."""

from __future__ import annotations

import argparse
import hashlib
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
        if source.get("kind") == "compatibility_fixture":
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
                contract.get("request_sha256", {}).get(version),
                contract.get("response_sha256", {}).get(version),
            ) if contract else None
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
        elif source.get("kind") == "native_methods_fixture":
            if source_data.get("native_version") != version:
                raise ValueError(
                    f"native version provenance mismatch for Codex {version}"
                )
            for key in (
                "binary_sha256",
                "source_tag",
                "source_url",
                "source_asset_sha256",
                "source_asset_id",
            ):
                value = source.get(key)
                if (
                    not isinstance(value, str)
                    or not value
                    or source_data.get(key) != value
                ):
                    raise ValueError(f"{key} provenance mismatch for Codex {version}")
                if key in {
                    "binary_sha256",
                    "source_asset_sha256",
                } and not _SHA256.fullmatch(value):
                    raise ValueError(f"{key} provenance mismatch for Codex {version}")
            if (
                "qualification_run" in source
                and source_data.get("qualification_run") != source["qualification_run"]
            ):
                raise ValueError(
                    f"qualification_run provenance mismatch for Codex {version}"
                )
            if source_data.get("typed_regression_tests") != "passed":
                raise ValueError(
                    f"passed typed regressions required for Codex {version}"
                )
            methods = source_data.get("methods")
            contract = methods.get(method) if isinstance(methods, dict) else None
            if not isinstance(contract, dict):
                raise ValueError(f"method qualification missing for {version}/{method}")
            smoke = contract.get("protocol_smoke")
            if (
                not isinstance(smoke, dict)
                or smoke.get("status") != "passed"
                or smoke.get("positive") is not True
            ):
                raise ValueError(
                    f"positive live method evidence required for {version}/{method}"
                )
            schemas = (contract.get("request_schema"), contract.get("response_schema"))
            if not all(isinstance(schema, dict) for schema in schemas):
                raise ValueError(
                    f"request and response schema objects required for {version}/{method}"
                )
            expected = tuple(
                hashlib.sha256(
                    json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                for schema in schemas
            )
        else:
            raise ValueError(f"unknown provenance kind for Codex {version}")
        if expected != (request_hash, response_hash):
            raise ValueError(f"schema fingerprint mismatch for {version}/{method}")
        methods_by_version.setdefault(version, set()).add(method)

    if set(methods_by_version) != set(sources):
        raise ValueError("manifest provenance contains an unreferenced version")
    return methods_by_version


def render_module(methods_by_version: dict[str, set[str]]) -> str:
    lines = [
        '"""Generated by ci/generate_native_qualification.py; do not edit."""',
        "",
        "REVIEWED_EXPERIMENTAL_METHODS_BY_NATIVE_VERSION = {",
    ]
    for version in sorted(methods_by_version):
        lines.extend(
            (f"    {json.dumps(version)}: frozenset(", "        {")
        )
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
