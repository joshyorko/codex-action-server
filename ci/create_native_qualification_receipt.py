#!/usr/bin/env python3
"""Create the immutable v1 handoff for a fully qualified, published CAS image."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_RUN_URL = re.compile(
    r"https://github\.com/joshyorko/codex-action-server/actions/runs/[1-9][0-9]*\Z"
)
_COMMIT_URL = re.compile(
    r"https://github\.com/joshyorko/codex-action-server/commit/[0-9a-f]{40}\Z"
)
_COMPARE_URL = re.compile(
    r"https://github\.com/joshyorko/codex-action-server/compare/[A-Za-z0-9._/-]+\Z"
)
_METHOD = re.compile(r"[A-Za-z][A-Za-z0-9_./-]{0,127}\Z")


def _read_json(path: Path):
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError(f"qualification input is missing or too large: {path.name}")
    return json.loads(path.read_text())


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repo_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError("qualification fixture must be a file inside the source tree")
    return path


def _version_key(version: str) -> tuple[int, int, int]:
    if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
        raise ValueError("qualification source version is not stable semantic version")
    return tuple(int(part) for part in version.split("."))


def _method_rows(fixture: dict, catalog: dict, version: str) -> dict[str, dict]:
    expected = {
        row["method"]: row
        for row in catalog.get("methods", [])
        if isinstance(row, dict) and isinstance(row.get("method"), str)
    }
    if len(expected) != len(catalog.get("methods", [])) or not expected:
        raise ValueError("native method catalog is malformed")
    rows = fixture.get("methods")
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError(
            f"native method fixture does not cover the full catalog for {version}"
        )
    actual = {row.get("method"): row for row in rows if isinstance(row, dict)}
    if len(actual) != len(rows) or set(actual) != set(expected):
        raise ValueError(
            f"native method fixture method set differs from catalog for {version}"
        )
    for method, row in actual.items():
        if _METHOD.fullmatch(method) is None:
            raise ValueError(f"native method name is invalid for {version}")
        expected_row = expected[method]
        if (
            row.get("classification") != expected_row.get("classification")
            or row.get("request_schema") != expected_row.get("request_schema")
            or row.get("response_schema") != expected_row.get("response_schema")
            or row.get("smoke_strategy") != expected_row.get("smoke_strategy")
            or row.get("owning_typed_actions")
            != expected_row.get("owning_typed_actions")
            or row.get("regression_tests") != expected_row.get("regression_tests")
        ):
            raise ValueError(
                f"native method fixture catalog mismatch for {version}/{method}"
            )
        if row.get("status") not in {
            "compatible_pending_review",
            "needs_review",
            "unqualified",
        }:
            raise ValueError(
                f"native method fixture status is invalid for {version}/{method}"
            )
        if row.get("status") != "compatible_pending_review":
            continue
        if row.get("typed_regressions") != "passed":
            raise ValueError(
                f"native method typed regressions are missing for {version}/{method}"
            )
        expected_tests = expected_row.get("regression_tests")
        coverage = row.get("typed_regression_coverage")
        if (
            not isinstance(expected_tests, list)
            or not expected_tests
            or not isinstance(coverage, dict)
            or set(coverage) != set(expected_tests)
        ):
            raise ValueError(
                f"native method regression coverage is missing for {version}/{method}"
            )
        passed_tests = 0
        for test_path, counts in coverage.items():
            if (
                not isinstance(counts, dict)
                or set(counts) != {"passed", "skipped", "failed"}
                or any(
                    type(counts.get(key)) is not int or counts[key] < 0
                    for key in ("passed", "skipped", "failed")
                )
                or counts["failed"] != 0
            ):
                raise ValueError(
                    f"native method regression coverage is invalid for {version}/{method}/{test_path}"
                )
            passed_tests += counts["passed"]
        if passed_tests == 0:
            raise ValueError(
                f"native method regression tests did not run for {version}/{method}"
            )
        fingerprint = row.get("schema_fingerprint")
        if not isinstance(fingerprint, dict) or any(
            not isinstance(fingerprint.get(key), str)
            or _SHA.fullmatch(fingerprint[key]) is None
            for key in ("request_sha256", "response_sha256")
        ):
            raise ValueError(
                f"native method schema fingerprints are missing for {version}/{method}"
            )
        schemas = row.get("schemas")
        if not isinstance(schemas, dict) or not all(
            isinstance(schemas.get(name), dict)
            for name in (row["request_schema"], row["response_schema"])
        ):
            raise ValueError(
                f"native method schemas are missing for {version}/{method}"
            )
        strategy = expected_row.get("smoke_strategy")
        smoke = row.get("smoke")
        if strategy == "disposable_protocol_smoke" and (
            not isinstance(smoke, dict) or smoke.get("status") != "passed"
        ):
            raise ValueError(f"native read smoke is missing for {version}/{method}")
        if strategy != "disposable_protocol_smoke" and (
            not isinstance(smoke, dict)
            or smoke.get("status") != "typed_action_regressions_passed"
        ):
            raise ValueError(
                f"isolated behavior evidence is missing for {version}/{method}"
            )
    return actual


def _canonical_receipt_digest(receipt: dict) -> str:
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    return _sha256_bytes(canonical)


def build_receipt(
    root: Path,
    *,
    repository: str,
    source_commit: str,
    image: str,
    image_digest: str,
    image_revision: str,
    qualification_run_url: str,
    required_ci_url: str,
    publication_run_url: str,
    comparison_url: str,
) -> dict | None:
    """Bind complete merged method qualification to one published image.

    Partial qualification is a normal no-receipt state. Invalid provenance or
    conflicting approval evidence is a hard error so a caller cannot hide a
    malformed contract behind that state.
    """
    root = root.resolve()
    if _COMMIT.fullmatch(source_commit) is None:
        raise ValueError("source commit must be a full lowercase Git SHA")
    if image_revision != source_commit:
        raise ValueError("image revision does not match source commit")
    if repository != "joshyorko/codex-action-server":
        raise ValueError("repository name is not the approved CAS source")
    if image != f"ghcr.io/{repository}":
        raise ValueError("receipt image must be the repository's GHCR image")
    if not isinstance(image_digest, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", image_digest
    ):
        raise ValueError("published image digest must be an immutable sha256 digest")
    if (
        not isinstance(qualification_run_url, str)
        or _RUN_URL.fullmatch(qualification_run_url) is None
    ):
        raise ValueError("qualification run URL is invalid")
    if (
        not isinstance(required_ci_url, str)
        or _RUN_URL.fullmatch(required_ci_url) is None
    ):
        raise ValueError("required CI run URL is invalid")
    if (
        not isinstance(publication_run_url, str)
        or _RUN_URL.fullmatch(publication_run_url) is None
    ):
        raise ValueError("publication run URL is invalid")
    if (
        not isinstance(comparison_url, str)
        or _COMPARE_URL.fullmatch(comparison_url) is None
    ):
        raise ValueError("source comparison URL is invalid")

    manifest_path = root / "ci/codex_qualification/approved_methods.json"
    catalog_path = root / "ci/codex_qualification/native_methods.json"
    manifest = _read_json(manifest_path)
    catalog = _read_json(catalog_path)
    if manifest.get("schema_version") != 1 or catalog.get("schema_version") != 1:
        raise ValueError("unsupported qualification manifest or catalog version")
    catalog_methods = catalog.get("methods")
    if not isinstance(catalog_methods, list) or not catalog_methods:
        raise ValueError("native method catalog is empty or malformed")

    sources = manifest.get("sources")
    if not isinstance(sources, dict):
        raise ValueError("native qualification sources are malformed")
    candidates = []
    for version, source in sources.items():
        fixture_ref = source.get("native_methods_fixture")
        if not fixture_ref and source.get("kind") == "native_methods_fixture":
            fixture_ref = source.get("fixture")
        if isinstance(fixture_ref, str):
            candidates.append((_version_key(version), version, source, fixture_ref))
    if not candidates:
        return None

    _, version, source, fixture_ref = max(candidates, key=lambda item: item[0])
    fixture_path = _repo_file(root, fixture_ref)
    fixture = _read_json(fixture_path)
    if fixture.get("native_version") != version:
        raise ValueError(f"native method fixture version mismatch for {version}")
    if fixture.get("typed_regression_status") != "passed":
        return None
    if fixture.get("binary_sha256") != source.get("binary_sha256"):
        raise ValueError(f"native binary provenance mismatch for {version}")
    for key in ("source_tag", "source_url", "source_asset_sha256", "source_asset_id"):
        if fixture.get(key) != source.get(key):
            raise ValueError(f"native release provenance mismatch for {version}/{key}")
    upstream_qualification_run = source.get(
        "native_methods_qualification_run", source.get("qualification_run")
    )
    if (
        not isinstance(upstream_qualification_run, str)
        or _RUN_URL.fullmatch(upstream_qualification_run) is None
    ):
        raise ValueError(
            f"native qualification run provenance is invalid for {version}"
        )
    if fixture.get("qualification_run") != upstream_qualification_run:
        raise ValueError(f"qualification run mismatch for {version}")
    source_tag = f"rust-v{version}"
    expected_url = (
        f"https://github.com/openai/codex/releases/download/{source_tag}/"
        "codex-x86_64-unknown-linux-musl.tar.gz"
    )
    if (
        fixture.get("source_tag") != source_tag
        or fixture.get("source_url") != expected_url
    ):
        raise ValueError(
            f"native source is not an exact stable upstream release: {version}"
        )
    for key in ("source_asset_sha256", "binary_sha256"):
        if (
            not isinstance(fixture.get(key), str)
            or _SHA.fullmatch(fixture[key]) is None
        ):
            raise ValueError(f"native release digest is invalid for {version}/{key}")
    if not str(fixture.get("source_asset_id", "")).isdecimal():
        raise ValueError(f"native release asset ID is invalid for {version}")

    rows = _method_rows(fixture, catalog, version)
    if any(row.get("status") != "compatible_pending_review" for row in rows.values()):
        return None

    approvals_by_method = {}
    for approval in manifest.get("approvals", []):
        if approval.get("version") == version:
            method = approval.get("method")
            if not isinstance(method, str) or method in approvals_by_method:
                raise ValueError(
                    f"duplicate or malformed runtime approval for {version}"
                )
            approvals_by_method[method] = approval
    catalog_by_method = {row["method"]: row for row in catalog_methods}
    method_verdicts = {}
    behavior_coverage = {}
    surface_methods = []
    for method, row in sorted(rows.items()):
        approval = approvals_by_method.get(method)
        if (
            catalog_by_method[method]["classification"] == "experimental"
            and approval is None
        ):
            return None
        if approval is not None:
            if approval.get("provenance_ref") != version:
                raise ValueError(
                    f"runtime approval provenance mismatch for {version}/{method}"
                )
            if approval.get("schema_fingerprint") != row.get("schema_fingerprint"):
                raise ValueError(
                    f"runtime approval fingerprint differs from evidence for {version}/{method}"
                )
            evidence_ref = approval.get("method_evidence_ref")
            if evidence_ref is not None and evidence_ref != fixture_ref:
                raise ValueError(
                    f"runtime approval points to a different evidence fixture for {version}/{method}"
                )
            method_verdicts[method] = "already_approved"
        else:
            method_verdicts[method] = "qualified"

        strategy = catalog_by_method[method]["smoke_strategy"]
        behavior_coverage[method] = {
            "result": "passed",
            "strategy": "native_read_smoke"
            if strategy == "disposable_protocol_smoke"
            else "isolated_fixture",
        }
        owner = (
            "typed_action"
            if catalog_by_method[method].get("owning_typed_actions")
            else "shared_helper"
        )
        surface_methods.append({"name": method, "owner": owner})

    surface_methods.sort(key=lambda item: item["name"])
    surface_payload = json.dumps(
        surface_methods, sort_keys=True, separators=(",", ":")
    ).encode()
    surface_sha256 = _sha256_bytes(surface_payload)
    receipt = {
        "schema_version": 1,
        "source": {
            "repository": repository,
            "revision": source_commit,
            "merged": True,
            "commit_url": f"https://github.com/{repository}/commit/{source_commit}",
        },
        "qualification": {
            "status": "qualified",
            "run_url": qualification_run_url,
            "required_ci_url": required_ci_url,
            "comparison_url": comparison_url,
            "native_release": {
                "version": version,
                "tag": fixture["source_tag"],
                "asset_url": fixture["source_url"],
                "asset_id": fixture["source_asset_id"],
                "asset_sha256": fixture["source_asset_sha256"],
                "binary_sha256": fixture["binary_sha256"],
                "fixture_path": fixture_ref,
                "fixture_sha256": _sha256(fixture_path),
                "qualification_run_url": upstream_qualification_run,
            },
            "surface": {
                "complete": True,
                "sha256": surface_sha256,
                "methods": surface_methods,
            },
            "method_verdicts": method_verdicts,
            "behavior_coverage": behavior_coverage,
            "withheld_methods": [],
        },
        "publication": {
            "status": "published",
            "run_url": publication_run_url,
            "image_repository": image,
            "image_tag": f"sha-{source_commit}",
            "digest": image_digest,
            "revision": image_revision,
            "platform": "linux/amd64",
        },
    }
    receipt["receipt_sha256"] = _canonical_receipt_digest(receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--image-revision", required=True)
    parser.add_argument("--qualification-run-url", required=True)
    parser.add_argument("--required-ci-url", required=True)
    parser.add_argument("--publication-run-url", required=True)
    parser.add_argument("--comparison-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    receipt = build_receipt(
        args.root,
        repository=args.repository,
        source_commit=args.source_commit,
        image=args.image,
        image_digest=args.image_digest,
        image_revision=args.image_revision,
        qualification_run_url=args.qualification_run_url,
        required_ci_url=args.required_ci_url,
        publication_run_url=args.publication_run_url,
        comparison_url=args.comparison_url,
    )
    if receipt is None:
        print(json.dumps({"status": "skipped_incomplete_qualification"}))
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps({"status": "created", "receipt_sha256": receipt["receipt_sha256"]})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
