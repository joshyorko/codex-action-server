#!/usr/bin/env python3
"""Prepare a deduplicated, review-only PR patch from native-method evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "ci/codex_qualification/native_methods.json"
MANIFEST = ROOT / "ci/codex_qualification/approved_methods.json"
VERSION_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z"
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
MAX_JSON_BYTES = 8 * 1024 * 1024


def _read_json(path: Path):
    if not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError(f"qualification input is missing or too large: {path.name}")
    return json.loads(path.read_text())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _render_runtime_gate(methods_by_version: dict[str, set[str]]) -> str:
    sys.path.insert(0, str(ROOT / "ci"))
    from generate_native_qualification import render_module

    return render_module(methods_by_version)


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _safe_repo_file(path: str) -> Path:
    resolved = (ROOT / path).resolve()
    if ROOT not in resolved.parents or not resolved.is_file():
        raise ValueError("qualification fixture path must be a repository file")
    return resolved


def _validate_method_evidence(
    artifact: Path, provenance: dict, report: dict
) -> list[dict]:
    catalog = _read_json(CATALOG).get("methods")
    if not isinstance(catalog, list):
        raise ValueError("native method catalog is malformed")
    expected = {row["method"]: row for row in catalog}
    rows = report.get("methods")
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError(
            "qualification evidence does not cover the complete CAS method catalog"
        )
    by_method = {row.get("method"): row for row in rows if isinstance(row, dict)}
    if len(by_method) != len(rows) or set(by_method) != set(expected):
        raise ValueError(
            "qualification evidence method set differs from the CAS catalog"
        )
    if report.get("version") != provenance.get("version"):
        raise ValueError("method evidence version does not match binary provenance")
    if report.get("source_tag") != provenance.get("source_tag"):
        raise ValueError("method evidence tag does not match source provenance")
    if report.get("source_url") != provenance.get("source_url"):
        raise ValueError("method evidence asset URL does not match source provenance")
    if report.get("source_asset_sha256") != provenance.get("source_asset_sha256"):
        raise ValueError(
            "method evidence asset digest does not match source provenance"
        )
    if report.get("source_asset_id") != provenance.get("source_asset_id"):
        raise ValueError("method evidence asset ID does not match source provenance")
    if report.get("binary_sha256") != provenance.get("binary_sha256"):
        raise ValueError(
            "method evidence binary digest does not match source provenance"
        )

    typed = provenance.get("typed_regression_evidence")
    if not isinstance(typed, dict) or typed.get("schema_version") != 1:
        raise ValueError("typed regression evidence is missing")
    if typed.get("candidate_version") != provenance.get("version"):
        raise ValueError("typed regression evidence belongs to another candidate")
    if typed.get("status") != "passed":
        raise ValueError("typed regression suite did not pass")
    test_hashes = typed.get("test_files", {})

    for method, catalog_row in expected.items():
        evidence = by_method[method]
        if (
            evidence.get("request_schema") != catalog_row["request_schema"]
            or evidence.get("response_schema") != catalog_row["response_schema"]
            or evidence.get("classification") != catalog_row["classification"]
        ):
            raise ValueError(f"method evidence catalog mismatch for {method}")
        if evidence.get("status") not in {
            "compatible_pending_review",
            "needs_review",
            "unqualified",
        }:
            raise ValueError(f"unknown qualification status for {method}")

        typed_row = typed.get("methods", {}).get(method, {})
        required_tests = sorted(set(catalog_row["regression_tests"]))
        if (
            evidence.get("typed_regressions") != "passed"
            or typed_row.get("status") != "passed"
            or typed_row.get("test_files") != required_tests
        ):
            if evidence.get("status") == "compatible_pending_review":
                raise ValueError(
                    f"method-specific typed regressions did not pass for {method}"
                )

        for test_name in required_tests:
            test_path = _safe_repo_file(test_name)
            if evidence.get(
                "status"
            ) == "compatible_pending_review" and test_hashes.get(test_name) != _sha256(
                test_path
            ):
                raise ValueError(f"typed regression evidence is stale for {method}")

        if evidence.get("status") != "compatible_pending_review":
            continue
        if evidence.get("reason_code") != "schema_and_behavior_passed":
            raise ValueError(f"compatible method has no pass reason for {method}")
        fingerprint = evidence.get("schema_fingerprint")
        if not isinstance(fingerprint, dict) or any(
            not isinstance(fingerprint.get(key), str)
            or SHA256_PATTERN.fullmatch(fingerprint[key]) is None
            for key in ("request_sha256", "response_sha256")
        ):
            raise ValueError(f"method schema fingerprints are incomplete for {method}")
        if catalog_row["smoke_strategy"] == "disposable_protocol_smoke":
            if evidence.get("smoke", {}).get("status") != "passed":
                raise ValueError(f"native protocol smoke did not pass for {method}")
        for key in ("request_schema", "response_schema"):
            schema_name = catalog_row[key]
            schema_path = artifact / "candidate-schemas" / f"{schema_name}.json"
            schema = _read_json(schema_path)
            if schema.get("title") != schema_name:
                raise ValueError(f"candidate schema title mismatch for {schema_name}")
            if (
                _sha256(schema_path)
                != fingerprint[
                    "request_sha256" if key == "request_schema" else "response_sha256"
                ]
            ):
                raise ValueError(
                    f"candidate schema fingerprint mismatch for {schema_name}"
                )
    return [by_method[method] for method in expected]


def _render_docs(
    document: str, fixture: dict, method_rows: list[dict], run_url: str
) -> str:
    version = fixture["native_version"]
    marker = f"<!-- codex-qualification:{version} -->"
    if marker in document:
        return document
    qualified = [
        row["method"]
        for row in method_rows
        if row["status"] == "compatible_pending_review"
    ]
    withheld = [
        row["method"]
        for row in method_rows
        if row["status"] != "compatible_pending_review"
    ]
    section = "\n".join(
        (
            marker,
            f"## Qualified candidate: Codex {version}",
            "",
            f"- Source release: [`{fixture['source_tag']}`]({fixture['source_url']}); asset ID `{fixture['source_asset_id']}`, SHA-256 `{fixture['source_asset_sha256']}`.",
            f"- Binary SHA-256: `{fixture['binary_sha256']}`.",
            f"- Method evidence: {len(qualified)} compatible pending human review; {len(withheld)} withheld.",
            f"- Compatible methods: {', '.join(f'`{method}`' for method in qualified) or 'none'}.",
            f"- Withheld methods: {', '.join(f'`{method}`' for method in withheld) or 'none'}.",
            f"- Typed regressions and disposable protocol smokes are recorded in [qualification run]({run_url}).",
            "- This evidence does not update installed Codex daemons, claim deployment state, or admit withheld methods.",
        )
    )
    return document.rstrip() + "\n\n" + section + "\n"


def prepare_proposal(artifact: Path, output: Path, run_url: str) -> dict:
    provenance = _read_json(artifact / "provenance.json")
    method_report = _read_json(artifact / "native-methods.json")
    release = _read_json(artifact / "source-release.json")
    version = provenance.get("version")
    if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError("candidate version is not a stable semantic version")
    expected_tag = f"rust-v{version}"
    expected_url = (
        f"https://github.com/openai/codex/releases/download/{expected_tag}/"
        "codex-x86_64-unknown-linux-musl.tar.gz"
    )
    if (
        provenance.get("source_tag") != expected_tag
        or provenance.get("source_url") != expected_url
    ):
        raise ValueError(
            "candidate provenance is not the exact upstream stable release"
        )
    asset = release.get("asset", {})
    if (
        release.get("tag_name") != expected_tag
        or asset.get("browser_download_url") != expected_url
        or asset.get("digest", "").removeprefix("sha256:")
        != provenance.get("source_asset_sha256")
        or str(asset.get("id")) != provenance.get("source_asset_id")
        or not isinstance(asset.get("id"), int)
    ):
        raise ValueError(
            "candidate source-release metadata does not match qualification"
        )
    for key in ("source_asset_sha256", "binary_sha256"):
        if (
            not isinstance(provenance.get(key), str)
            or SHA256_PATTERN.fullmatch(provenance[key]) is None
        ):
            raise ValueError(f"candidate {key} is missing or invalid")

    method_rows = _validate_method_evidence(artifact, provenance, method_report)
    qualified = [
        row for row in method_rows if row["status"] == "compatible_pending_review"
    ]
    if not qualified:
        raise ValueError("candidate has no individually qualified native methods")

    generated_hashes = provenance.get("generated_files_sha256", {})
    fixture_methods = []
    for row in method_rows:
        catalog_row = next(
            item
            for item in _read_json(CATALOG)["methods"]
            if item["method"] == row["method"]
        )
        fixture_row = {
            key: row[key]
            for key in (
                "method",
                "classification",
                "request_schema",
                "response_schema",
                "status",
                "reason_code",
                "schema_fingerprint",
                "schema_diff",
                "typed_regressions",
                "smoke_strategy",
                "smoke",
                "owning_typed_actions",
                "regression_tests",
            )
        }
        schemas = {}
        for key in ("request_schema", "response_schema"):
            schema_name = catalog_row[key]
            schema_path = artifact / "candidate-schemas" / f"{schema_name}.json"
            if schema_path.is_file():
                schemas[schema_name] = _read_json(schema_path)
        fixture_row["schemas"] = schemas
        fixture_methods.append(fixture_row)

    typed = provenance["typed_regression_evidence"]
    fixture_relative = f"tests/fixtures/protocol/codex_{version}_native_methods.json"
    fixture = {
        "schema_version": 1,
        "native_version": version,
        "source_tag": expected_tag,
        "source_url": expected_url,
        "source_asset_sha256": provenance["source_asset_sha256"],
        "source_asset_id": str(provenance["source_asset_id"]),
        "binary_sha256": provenance["binary_sha256"],
        "qualification_run": run_url,
        "generation_command": "codex app-server generate-json-schema --experimental --out <private-temporary-directory>",
        "generated_files_sha256": generated_hashes,
        "typed_regression_status": typed.get("status"),
        "typed_regression_test_files_sha256": typed.get("test_files", {}),
        "methods": fixture_methods,
        "counts": method_report.get("counts", {}),
    }

    manifest = _read_json(MANIFEST)
    existing_source = manifest["sources"].get(version)
    had_native_fixture = bool(
        existing_source
        and (
            existing_source.get("native_methods_fixture")
            or existing_source.get("kind") == "native_methods_fixture"
        )
    )
    source_fields = {
        "source_tag": expected_tag,
        "source_url": expected_url,
        "source_asset_sha256": provenance["source_asset_sha256"],
        "source_asset_id": str(provenance["source_asset_id"]),
        "binary_sha256": provenance["binary_sha256"],
    }
    if existing_source:
        for key, value in source_fields.items():
            if existing_source.get(key) != value:
                raise ValueError(
                    "candidate version already has different source provenance"
                )
        existing_fixture = existing_source.get("native_methods_fixture")
        if existing_fixture:
            existing_path = _safe_repo_file(existing_fixture)
            current = _read_json(existing_path)
            if (
                current.get("native_version") != version
                or current.get("binary_sha256") != provenance["binary_sha256"]
            ):
                raise ValueError(
                    "existing native-method fixture conflicts with this source"
                )
            fixture_relative = existing_fixture
        elif existing_source.get("kind") == "native_methods_fixture":
            fixture_relative = existing_source["fixture"]
            existing_path = _safe_repo_file(fixture_relative)
            current = _read_json(existing_path)
            if current.get("binary_sha256") != provenance["binary_sha256"]:
                raise ValueError(
                    "existing native-method fixture conflicts with this source"
                )
    else:
        manifest["sources"][version] = {
            **source_fields,
            "fixture": fixture_relative,
            "kind": "native_methods_fixture",
            "qualification_run": run_url,
            "native_methods_fixture": fixture_relative,
            "native_methods_qualification_run": run_url,
        }

    if existing_source:
        existing_source.setdefault("native_methods_fixture", fixture_relative)
        existing_source.setdefault("native_methods_qualification_run", run_url)

    already = {
        (item.get("version"), item.get("method")) for item in manifest["approvals"]
    }
    approval_methods = [
        row
        for row in qualified
        if row["classification"] == "experimental"
        or row["method"] == "server/diagnostics"
    ]
    new_approvals = []
    for row in approval_methods:
        key = (version, row["method"])
        if key in already:
            continue
        manifest["approvals"].append(
            {
                "version": version,
                "method": row["method"],
                "provenance_ref": version,
                "method_evidence_ref": fixture_relative,
                "schema_fingerprint": row["schema_fingerprint"],
            }
        )
        new_approvals.append(row["method"])

    # Preserve reviewed method records while allowing a same-version PR to add
    # newly qualified methods. This makes reruns idempotent and retains every
    # existing exact-version approval.
    if had_native_fixture:
        old_fixture = _read_json(_safe_repo_file(fixture_relative))
        old_rows = {row.get("method"): row for row in old_fixture.get("methods", [])}
        retained = []
        for row in fixture["methods"]:
            if (version, row["method"]) in already and row["method"] in old_rows:
                retained.append(old_rows[row["method"]])
            else:
                retained.append(row)
        fixture["methods"] = retained
        fixture["qualification_run"] = existing_source.get(
            "native_methods_qualification_run",
            existing_source.get("qualification_run", run_url),
        )
    if existing_source:
        existing_source["native_methods_fixture"] = fixture_relative
        existing_source["native_methods_qualification_run"] = fixture[
            "qualification_run"
        ]
    _write_json(output / fixture_relative, fixture)

    methods_by_version: dict[str, set[str]] = {}
    for approval in manifest["approvals"]:
        methods_by_version.setdefault(approval["version"], set()).add(
            approval["method"]
        )
    generated_module = _render_runtime_gate(methods_by_version)
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "ci/codex_qualification/approved_methods.json", manifest)
    module_path = output / "src/codex_shared/native_qualification.py"
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(generated_module)
    docs_path = ROOT / "docs/acceptance/codex-native-qualification.md"
    docs = _render_docs(docs_path.read_text(), fixture, method_rows, run_url)
    docs_output = output / "docs/acceptance/codex-native-qualification.md"
    docs_output.parent.mkdir(parents=True, exist_ok=True)
    docs_output.write_text(docs)

    files = [
        fixture_relative,
        "ci/codex_qualification/approved_methods.json",
        "src/codex_shared/native_qualification.py",
        "docs/acceptance/codex-native-qualification.md",
    ]
    result = {
        "status": "prepared",
        "version": version,
        "qualified_methods": [row["method"] for row in qualified],
        "new_approvals": new_approvals,
        "withheld_methods": method_report.get("withheld_methods", []),
        "files": files,
    }
    if had_native_fixture and not new_approvals:
        result.update(status="already-approved", files=[])
        for relative in (fixture_relative,):
            (output / relative).unlink(missing_ok=True)
    _write_json(output / "proposal.json", result)
    return result


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
