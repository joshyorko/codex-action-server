#!/usr/bin/env python3
"""Generate schemas and exercise a pinned Codex candidate's diagnostics RPC."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import select
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests/fixtures/protocol/codex_0.162.1_server_diagnostics.json"
CATALOG = ROOT / "ci/codex_qualification/native_methods.json"
APPROVALS = ROOT / "ci/codex_qualification/approved_methods.json"
METHOD = "server/diagnostics"
IGNORED_KEYS = {"$schema", "description", "title"}


def normalize(value):
    if isinstance(value, dict):
        return {
            key: normalize(item)
            for key, item in sorted(value.items())
            if key not in IGNORED_KEYS
        }
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


def schema_files(directory: Path) -> dict[str, dict]:
    result = {}
    for path in directory.rglob("*.json"):
        try:
            schema = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        title = schema.get("title") if isinstance(schema, dict) else None
        if isinstance(title, str) and title:
            if title in result:
                raise ValueError(f"candidate schema title is duplicated: {title}")
            result[title] = (schema, path)
    return result


def sha256_file(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


MAX_JSON_LINE_BYTES = 1024 * 1024
RPC_TIMEOUT_SECONDS = 20
VERSION_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z"
)


def _write_jsonrpc(process, message):
    if process.stdin is None:
        raise RuntimeError("app-server stdin is unavailable")
    process.stdin.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
    process.stdin.flush()


def _read_jsonrpc_response(process, buffer, request_id):
    deadline = time.monotonic() + RPC_TIMEOUT_SECONDS
    while True:
        newline = buffer.find(b"\n")
        if newline >= 0:
            line = bytes(buffer[:newline])
            del buffer[: newline + 1]
            if len(line) > MAX_JSON_LINE_BYTES:
                raise ValueError("app-server response exceeded the line limit")
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError("app-server response is not an object")
            if message.get("id") == request_id:
                return message
            if message.get("id") is not None:
                raise ValueError("unexpected app-server request")
            continue
        if len(buffer) > MAX_JSON_LINE_BYTES:
            raise ValueError("app-server response exceeded the line limit")
        if process.poll() is not None:
            raise RuntimeError("app-server exited before replying")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("app-server response timed out")
        readable, _, _ = select.select([process.stdout], [], [], remaining)
        if not readable:
            raise TimeoutError("app-server response timed out")
        chunk = os.read(process.stdout.fileno(), 4096)
        if not chunk:
            raise RuntimeError("app-server closed stdout")
        buffer.extend(chunk)


def live_protocol_smoke(
    binary: Path,
    temporary: Path,
    *,
    method: str = METHOD,
    params: dict[str, object] | None = None,
    response_schema: dict | None = None,
) -> dict[str, object]:
    """Call one safe method against a fresh, no-credentials app-server home."""
    from provider_acceptance import valid_native_diagnostics

    home = temporary / "home"
    codex_home = home / "codex"
    project = temporary / "project"
    home.mkdir(parents=True, exist_ok=True)
    codex_home.mkdir()
    project.mkdir(parents=True)
    environment = {
        "PATH": os.pathsep.join((str(binary.parent), os.environ.get("PATH", ""))),
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "XDG_CACHE_HOME": str(home / "cache"),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_DATA_HOME": str(home / "data"),
    }
    for name in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        Path(environment[name]).mkdir()

    process = None
    evidence = {"method": method, "status": "failed", "failure": "not_started"}
    try:
        process = subprocess.Popen(
            [str(binary), "app-server", "--listen", "stdio://"],
            cwd=home,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        buffer = bytearray()
        _write_jsonrpc(
            process,
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {
                        "name": "cas-codex-qualification",
                        "title": "CAS Codex qualification",
                        "version": "1",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            },
        )
        initialized = _read_jsonrpc_response(process, buffer, 1)
        if not isinstance(initialized.get("result"), dict):
            evidence = {"method": method, "status": "initialize_failed"}
        else:
            _write_jsonrpc(process, {"method": "initialized", "params": {}})
            _write_jsonrpc(
                process,
                {"id": 2, "method": method, "params": params or {}},
            )
            response = _read_jsonrpc_response(process, buffer, 2)
            result = response.get("result")
            if "error" in response:
                evidence = {
                    "method": method,
                    "status": "failed",
                    "failure": "native_rpc_error",
                }
            elif response_schema is not None and not matches_json_schema(
                result, response_schema
            ):
                evidence = {"method": method, "status": "invalid_response"}
            elif method == METHOD and not valid_native_diagnostics(result):
                evidence = {"method": method, "status": "invalid_response"}
            else:
                evidence = {"method": method, "status": "passed"}
                if method == METHOD:
                    process_info = result.get("process")
                    gauges = result.get("gauges")
                    evidence["response_shape"] = {
                        "process_id_is_integer": isinstance(process_info, dict)
                        and type(process_info.get("id")) is int,
                        "gauges_is_list": isinstance(gauges, list),
                        "gauge_count": len(gauges)
                        if isinstance(gauges, list)
                        else None,
                    }
    except (
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        TimeoutError,
        ValueError,
    ) as error:
        evidence = {
            "method": method,
            "status": "failed",
            "failure": type(error).__name__,
        }
    finally:
        if process is None:
            cleanup = "not_started"
        elif process.poll() is not None:
            cleanup = "exited"
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
                cleanup = "terminated"
            except (OSError, subprocess.TimeoutExpired) as error:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                    cleanup = "killed"
                except (OSError, subprocess.TimeoutExpired):
                    cleanup = "failed"
                if cleanup == "failed":
                    evidence["cleanup_failure"] = type(error).__name__
        evidence["cleanup"] = cleanup
        if cleanup not in {"exited", "terminated", "killed"}:
            evidence["status"] = "cleanup_failed"
    return evidence


def matches_json_schema(
    value, schema: dict, root: dict | None = None, depth: int = 0
) -> bool:
    """Validate the JSON Schema subset emitted for native RPC method types."""
    if depth > 64 or not isinstance(schema, dict):
        return False
    root = schema if root is None else root
    reference = schema.get("$ref")
    if reference is not None:
        if not isinstance(reference, str) or not reference.startswith("#/definitions/"):
            return False
        target = root.get("definitions", {}).get(
            reference.removeprefix("#/definitions/")
        )
        return isinstance(target, dict) and matches_json_schema(
            value, target, root, depth + 1
        )
    for keyword in ("allOf", "anyOf", "oneOf"):
        branches = schema.get(keyword)
        if branches is not None:
            if not isinstance(branches, list):
                return False
            outcomes = [
                matches_json_schema(value, branch, root, depth + 1)
                for branch in branches
            ]
            if keyword == "allOf" and not all(outcomes):
                return False
            if keyword == "anyOf" and not any(outcomes):
                return False
            if keyword == "oneOf" and sum(outcomes) != 1:
                return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if "const" in schema and value != schema["const"]:
        return False
    expected = schema.get("type")
    types = expected if isinstance(expected, list) else [expected] if expected else []
    if types and not any(_json_type_matches(value, item) for item in types):
        return False
    if isinstance(value, dict):
        if any(required not in value for required in schema.get("required", [])):
            return False
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return False
        for key, item in value.items():
            if key in properties:
                if not matches_json_schema(item, properties[key], root, depth + 1):
                    return False
            elif schema.get("additionalProperties") is False:
                return False
    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict) and any(
            not matches_json_schema(item, item_schema, root, depth + 1)
            for item in value
        ):
            return False
        if len(value) < schema.get("minItems", 0):
            return False
        if isinstance(schema.get("maxItems"), int) and len(value) > schema["maxItems"]:
            return False
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            return False
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return False
        if "maximum" in schema and value > schema["maximum"]:
            return False
    return True


def _json_type_matches(value, expected: str) -> bool:
    checks = {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda: isinstance(value, (int, float))
        and not isinstance(value, bool),
        "boolean": lambda: isinstance(value, bool),
        "null": lambda: value is None,
    }
    return checks.get(expected, lambda: False)()


def _write_json(path: Path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _copy_candidate_schemas(files, output: Path):
    destination = output / "candidate-schemas"
    destination.mkdir(parents=True, exist_ok=True)
    for _, path in files.values():
        shutil.copyfile(path, destination / path.name)


def _load_method_catalog() -> list[dict]:
    value = json.loads(CATALOG.read_text())
    methods = value.get("methods")
    if value.get("schema_version") != 1 or not isinstance(methods, list):
        raise ValueError("native method catalog is malformed")
    seen = set()
    for row in methods:
        if not isinstance(row, dict) or not all(
            isinstance(row.get(field), str) and row[field]
            for field in (
                "method",
                "request_schema",
                "response_schema",
                "classification",
                "smoke_strategy",
            )
        ):
            raise ValueError("native method catalog row is malformed")
        if row["method"] in seen:
            raise ValueError("native method catalog contains a duplicate RPC")
        seen.add(row["method"])
    return methods


def _latest_approval(
    manifest: dict, method: str, *, before_version: str | None = None
) -> tuple[str, dict] | None:
    before_key = _version_key(before_version) if before_version is not None else None
    approvals = []
    for item in manifest.get("approvals", []):
        if item.get("method") != method:
            continue
        version = item.get("version")
        version_key = _version_key(version)
        if version_key is None or (
            before_key is not None and version_key >= before_key
        ):
            continue
        approvals.append((version_key, version, item))
    if not approvals:
        return None
    _, version, approval = max(approvals, key=lambda item: item[0])
    return version, approval


def _version_key(version: str) -> tuple[int, int, int] | None:
    if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None:
        return None
    return tuple(int(part) for part in version.split("."))


def _latest_method_contract(
    manifest: dict, method: str, candidate_version: str
) -> tuple[str, dict, dict[str, dict]] | None:
    """Choose the newest merged schema contract before this candidate.

    Exact runtime approvals are one source of schema baselines. A merged native
    method evidence fixture is also a reviewed schema snapshot, including for
    methods that remain withheld from runtime admission.
    """
    candidate_key = _version_key(candidate_version)
    if candidate_key is None:
        return None
    options = []
    approval = _latest_approval(manifest, method, before_version=candidate_version)
    if approval is not None:
        version, row = approval
        version_key = _version_key(version)
        if version_key is not None and version_key < candidate_key:
            schemas = _baseline_schemas(manifest, version, method)
            options.append(
                (version_key, 0, version, row["schema_fingerprint"], schemas)
            )

    for version, source in manifest.get("sources", {}).items():
        version_key = _version_key(version)
        if version_key is None or version_key >= candidate_key:
            continue
        fixture_ref = source.get("native_methods_fixture")
        if not fixture_ref and source.get("kind") == "native_methods_fixture":
            fixture_ref = source.get("fixture")
        if not isinstance(fixture_ref, str):
            continue
        fixture_path = (ROOT / fixture_ref).resolve()
        if ROOT not in fixture_path.parents:
            raise ValueError("method evidence fixture must stay inside the repository")
        try:
            fixture = json.loads(fixture_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if (
            fixture.get("native_version") != version
            or fixture.get("typed_regression_status") != "passed"
            or (
                source.get("binary_sha256")
                and fixture.get("binary_sha256") != source.get("binary_sha256")
            )
        ):
            continue
        row = next(
            (
                item
                for item in fixture.get("methods", [])
                if isinstance(item, dict) and item.get("method") == method
            ),
            None,
        )
        if not isinstance(row, dict):
            continue
        if row.get("typed_regressions") != "passed":
            continue
        schemas = row.get("schemas")
        fingerprint = row.get("schema_fingerprint")
        if not isinstance(schemas, dict) or not isinstance(fingerprint, dict):
            continue
        if not schemas:
            continue
        options.append((version_key, 1, version, fingerprint, schemas))

    if not options:
        return None
    _, _, version, fingerprint, schemas = max(
        options, key=lambda item: (item[0], item[1])
    )
    return version, fingerprint, schemas


def _baseline_schemas(manifest: dict, version: str, method: str) -> dict[str, dict]:
    source = manifest["sources"].get(version, {})
    fixture_ref = source.get("native_methods_fixture") or source.get("fixture", "")
    fixture_path = (ROOT / fixture_ref).resolve()
    if ROOT not in fixture_path.parents:
        raise ValueError("approval fixture must stay inside the repository")
    try:
        fixture = json.loads(fixture_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(fixture.get("methods"), list):
        row = next(
            (item for item in fixture["methods"] if item.get("method") == method),
            None,
        )
        if isinstance(row, dict) and isinstance(row.get("schemas"), dict):
            return row["schemas"]
    if source.get("kind") == "diagnostics_fixture" and fixture.get("method") == method:
        return fixture.get("schemas", {})
    return {}


def _typed_regression_status(
    evidence: dict, method: str, version: str, expected_files: list[str]
) -> str:
    if (
        not isinstance(evidence, dict)
        or evidence.get("schema_version") != 1
        or evidence.get("candidate_version") != version
        or evidence.get("status") != "passed"
    ):
        return "not_run"
    methods = evidence.get("methods", {})
    hashes = evidence.get("test_files", {})
    test_outcomes = evidence.get("test_outcomes")
    if (
        not isinstance(methods, dict)
        or not isinstance(hashes, dict)
        or not isinstance(test_outcomes, dict)
    ):
        return "invalid_evidence"
    row = methods.get(method)
    if not isinstance(row, dict) or row.get("status") != "passed":
        return "not_run"
    if row.get("test_files") != sorted(set(expected_files)):
        return "invalid_evidence"
    coverage = row.get("coverage")
    if not isinstance(coverage, dict) or set(coverage) != set(row["test_files"]):
        return "invalid_evidence"
    passed_tests = 0
    for name, counts in coverage.items():
        if (
            not isinstance(counts, dict)
            or set(counts) != {"passed", "skipped", "failed"}
            or any(
                type(counts.get(key)) is not int or counts[key] < 0
                for key in ("passed", "skipped", "failed")
            )
        ):
            return "invalid_evidence"
        if test_outcomes.get(name) != counts:
            return "invalid_evidence"
        if counts["failed"]:
            return "failed"
        passed_tests += counts["passed"]
    if passed_tests == 0:
        return "not_run"
    for name in row["test_files"]:
        path = ROOT / name
        if not path.is_file() or hashes.get(name) != sha256_file(path):
            return "invalid_evidence"
    return "passed"


def _smoke_params(method: str, temporary: Path) -> dict[str, object]:
    cwd = str(temporary / "project")
    return {
        "account/rateLimits/read": {},
        "app/list": {"limit": 1},
        "hooks/list": {"cwds": [cwd]},
        "mcpServerStatus/list": {"limit": 1},
        "model/list": {"limit": 1},
        "modelProvider/capabilities/read": {},
        "plugin/list": {"cwds": [cwd]},
        "server/diagnostics": {},
        "skills/list": {"cwds": [cwd]},
        "thread/list": {"cwd": cwd, "limit": 1},
        "thread/loaded/list": {"limit": 1},
        "threadSection/list": {"limit": 1},
    }.get(method, {})


def build_method_evidence(
    binary: Path,
    generated_files: dict,
    output: Path,
    *,
    version: str,
    asset_sha256: str,
    asset_id: str,
    typed_regression_evidence: dict,
    temporary: Path,
    diagnostics_smoke: dict[str, object],
) -> dict:
    """Evaluate every CAS-consumed method independently and fail closed."""
    output.mkdir(parents=True, exist_ok=True)
    catalog = _load_method_catalog()
    manifest = json.loads(APPROVALS.read_text())
    results = []
    used_schemas = {}
    for row in catalog:
        request_entry = generated_files.get(row["request_schema"])
        response_entry = generated_files.get(row["response_schema"])
        evidence = {
            "method": row["method"],
            "classification": row["classification"],
            "request_schema": row["request_schema"],
            "response_schema": row["response_schema"],
            "smoke_strategy": row["smoke_strategy"],
            "owning_typed_actions": row["owning_typed_actions"],
            "regression_tests": row["regression_tests"],
            "typed_regressions": _typed_regression_status(
                typed_regression_evidence,
                row["method"],
                version,
                row["regression_tests"],
            ),
            "typed_regression_coverage": typed_regression_evidence.get("methods", {})
            .get(row["method"], {})
            .get("coverage", {}),
            "schema_fingerprint": None,
            "schema_diff": None,
            "smoke": {"status": "not_run"},
        }
        if not request_entry or not response_entry:
            evidence.update(
                status="unqualified", reason_code="candidate_schema_missing"
            )
            results.append(evidence)
            continue

        request_schema, request_path = request_entry
        response_schema, response_path = response_entry
        used_schemas[row["request_schema"]] = request_entry
        used_schemas[row["response_schema"]] = response_entry
        candidate_hashes = {
            "request_sha256": sha256_file(request_path),
            "response_sha256": sha256_file(response_path),
        }
        evidence["schema_fingerprint"] = candidate_hashes
        if row["smoke_strategy"] == "disposable_protocol_smoke":
            if row["method"] == METHOD:
                smoke = diagnostics_smoke
            else:
                method_home = temporary / f"smoke-{len(results)}"
                try:
                    params = _smoke_params(row["method"], method_home)
                    if not matches_json_schema(params, request_schema):
                        smoke = {
                            "method": row["method"],
                            "status": "invalid_request_fixture",
                        }
                    else:
                        smoke = live_protocol_smoke(
                            binary,
                            method_home,
                            method=row["method"],
                            params=params,
                            response_schema=response_schema,
                        )
                except Exception as error:
                    smoke = {
                        "method": row["method"],
                        "status": "failed",
                        "failure": type(error).__name__,
                    }
            evidence["smoke"] = smoke
        baseline = _latest_method_contract(manifest, row["method"], version)
        if baseline is None:
            evidence.update(
                status="needs_review", reason_code="reviewed_baseline_missing"
            )
            evidence["schema_diff"] = {
                "baseline": "unavailable",
                "candidate": {
                    "request": normalize(request_schema),
                    "response": normalize(response_schema),
                },
            }
            results.append(evidence)
            continue

        baseline_version, baseline_hashes, baseline_schemas = baseline
        if baseline_schemas:
            baseline_request = baseline_schemas.get(row["request_schema"])
            baseline_response = baseline_schemas.get(row["response_schema"])
            request_identical = normalize(request_schema) == normalize(baseline_request)
            response_identical = normalize(response_schema) == normalize(
                baseline_response
            )
            schema_diff = {
                "baseline_version": baseline_version,
                "request_identical": request_identical,
                "response_identical": response_identical,
                "baseline": {
                    "request": normalize(baseline_request),
                    "response": normalize(baseline_response),
                },
                "candidate": {
                    "request": normalize(request_schema),
                    "response": normalize(response_schema),
                },
            }
        else:
            request_identical = (
                candidate_hashes["request_sha256"] == baseline_hashes["request_sha256"]
            )
            response_identical = (
                candidate_hashes["response_sha256"]
                == baseline_hashes["response_sha256"]
            )
            schema_diff = {
                "baseline_version": baseline_version,
                "baseline_fingerprints": baseline_hashes,
                "candidate_fingerprints": candidate_hashes,
                "request_identical": request_identical,
                "response_identical": response_identical,
                "comparison": "exact_generated_schema_sha256",
            }
        evidence["schema_diff"] = schema_diff

        if not request_identical or not response_identical:
            evidence.update(status="needs_review", reason_code="schema_changed")
        elif evidence["typed_regressions"] != "passed":
            reason = (
                "typed_regression_evidence_invalid"
                if evidence["typed_regressions"] == "invalid_evidence"
                else "typed_regressions_failed"
                if evidence["typed_regressions"] == "failed"
                else "typed_regressions_not_run"
            )
            evidence.update(status="unqualified", reason_code=reason)
        elif (
            row["smoke_strategy"] == "disposable_protocol_smoke"
            and evidence["smoke"].get("status") != "passed"
        ):
            evidence.update(status="unqualified", reason_code="protocol_smoke_failed")
        else:
            evidence.update(
                status="compatible_pending_review",
                reason_code="schema_and_behavior_passed",
            )
            if row["smoke_strategy"] == "typed_read_fixture":
                evidence["smoke"] = {
                    "status": "typed_action_regressions_passed",
                    "test_files": row["regression_tests"],
                }
            elif row["smoke_strategy"] == "disposable_mutation_fixture":
                evidence["smoke"] = {
                    "status": "typed_action_regressions_passed",
                    "test_files": row["regression_tests"],
                }
        results.append(evidence)

    compatible = [
        item for item in results if item["status"] == "compatible_pending_review"
    ]
    needs_review = [item for item in results if item["status"] == "needs_review"]
    withheld = [
        item["method"]
        for item in results
        if item["status"] != "compatible_pending_review"
    ]
    overall = (
        "compatible_pending_review"
        if compatible
        else "needs_review"
        if needs_review
        else "unqualified"
    )
    _copy_candidate_schemas(used_schemas, output)
    report = {
        "schema_version": 1,
        "version": version,
        "qualification": overall,
        "source_tag": f"rust-v{version}",
        "source_url": f"https://github.com/openai/codex/releases/download/rust-v{version}/codex-x86_64-unknown-linux-musl.tar.gz",
        "source_asset_sha256": asset_sha256.removeprefix("sha256:"),
        "source_asset_id": str(asset_id),
        "binary_sha256": sha256_file(binary),
        "methods": results,
        "withheld_methods": withheld,
        "counts": {
            "total": len(results),
            "compatible_pending_review": len(compatible),
            "needs_review": len(needs_review),
            "unqualified": sum(item["status"] == "unqualified" for item in results),
            "withheld": len(withheld),
        },
    }
    _write_json(output / "native-methods.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-tag", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--asset-sha256", required=True)
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--typed-regression-evidence", required=True, type=Path)
    args = parser.parse_args()

    expected_url = (
        f"https://github.com/openai/codex/releases/download/rust-v{args.version}/"
        "codex-x86_64-unknown-linux-musl.tar.gz"
    )
    if (
        VERSION_PATTERN.fullmatch(args.version) is None
        or args.source_tag != f"rust-v{args.version}"
        or args.source_url != expected_url
        or re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", args.asset_sha256) is None
        or not args.asset_id.isdecimal()
    ):
        raise ValueError(
            "candidate release provenance is not an exact stable Codex asset"
        )

    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="codex-schema-") as temporary:
        temporary_path = Path(temporary)
        home = temporary_path / "codex-home"
        generated = temporary_path / "generated"
        home.mkdir()
        codex_home = home / ".codex"
        codex_home.mkdir()
        generated.mkdir()
        environment = {
            "PATH": os.pathsep.join(
                (str(args.binary.parent), os.environ.get("PATH", ""))
            ),
            "HOME": str(home),
            "CODEX_HOME": str(codex_home),
            "XDG_CACHE_HOME": str(home / "cache"),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_DATA_HOME": str(home / "data"),
        }
        for name in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
            Path(environment[name]).mkdir()
        try:
            version_run = subprocess.run(
                [str(args.binary), "--version"],
                check=True,
                timeout=30,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            reported_versions = re.findall(
                r"(?<![0-9.])(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?![0-9.])",
                version_run.stdout,
            )
            if reported_versions != [args.version]:
                raise ValueError(
                    "Codex binary version does not match the pinned release"
                )
            subprocess.run(
                [
                    str(args.binary),
                    "app-server",
                    "generate-json-schema",
                    "--experimental",
                    "--out",
                    str(generated),
                ],
                check=True,
                timeout=180,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            files = schema_files(generated)
            generation_error = None
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            files = {}
            generation_error = type(error).__name__

        try:
            typed_regression_evidence = json.loads(
                args.typed_regression_evidence.read_text()
            )
        except (OSError, json.JSONDecodeError):
            typed_regression_evidence = {}
        if not isinstance(typed_regression_evidence, dict):
            typed_regression_evidence = {}

        baseline = json.loads(BASELINE.read_text())
        live_smoke = {"method": METHOD, "status": "not_run"}
        diagnostic_names = {"ServerDiagnosticsParams", "ServerDiagnosticsResponse"}
        difference = {
            "missing_schemas": sorted(diagnostic_names - files.keys()),
            "generation_error": generation_error,
        }
        if not difference["missing_schemas"]:
            candidate = {
                name: normalize(files[name][0]) for name in sorted(diagnostic_names)
            }
            reviewed = {
                name: normalize(schema) for name, schema in baseline["schemas"].items()
            }
            identical = candidate == reviewed
            difference = {
                "baseline": reviewed,
                "candidate": candidate,
                "identical": identical,
            }
            if identical:
                try:
                    live_smoke = live_protocol_smoke(
                        args.binary,
                        temporary_path / "smoke",
                        response_schema=files["ServerDiagnosticsResponse"][0],
                    )
                except Exception as error:
                    live_smoke = {
                        "method": METHOD,
                        "status": "failed",
                        "failure": type(error).__name__,
                    }
        _write_json(args.output / "server-diagnostics-schema-diff.json", difference)

        method_evidence = build_method_evidence(
            args.binary,
            files,
            args.output,
            version=args.version,
            asset_sha256=args.asset_sha256,
            asset_id=args.asset_id,
            typed_regression_evidence=typed_regression_evidence,
            temporary=temporary_path / "method-smoke",
            diagnostics_smoke=live_smoke,
        )
        catalog_schema_names = {
            name
            for row in _load_method_catalog()
            for name in (row["request_schema"], row["response_schema"])
        }
        generated_hashes = {
            f"{name}.json": sha256_file(files[name][1])
            for name in sorted(catalog_schema_names & files.keys())
        }
        provenance = {
            "method": METHOD,
            "version": args.version,
            "source_tag": args.source_tag,
            "source_url": args.source_url,
            "source_asset_sha256": args.asset_sha256.removeprefix("sha256:"),
            "source_asset_id": args.asset_id,
            "binary_sha256": sha256_file(args.binary),
            "generated_files_sha256": generated_hashes,
            "baseline_version": baseline["native_version"],
            "qualification": method_evidence["qualification"],
            "typed_regression_tests": typed_regression_evidence.get(
                "status", "not_run"
            ),
            "typed_regression_evidence": typed_regression_evidence,
            "live_protocol_smoke": live_smoke["status"],
            "native_method_evidence": "native-methods.json",
            "method_status_counts": method_evidence["counts"],
        }
        _write_json(args.output / "provenance.json", provenance)
        _write_json(args.output / "protocol-smoke.json", live_smoke)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
