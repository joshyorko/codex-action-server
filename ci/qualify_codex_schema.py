#!/usr/bin/env python3
"""Generate schemas and exercise a pinned Codex candidate's diagnostics RPC."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import select
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests/fixtures/protocol/codex_0.162.1_server_diagnostics.json"
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
        if title in {"ServerDiagnosticsParams", "ServerDiagnosticsResponse"}:
            result[title] = (schema, path)
    return result


def sha256_file(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


MAX_JSON_LINE_BYTES = 1024 * 1024
RPC_TIMEOUT_SECONDS = 20


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


def live_protocol_smoke(binary: Path, temporary: Path) -> dict[str, object]:
    from provider_acceptance import valid_native_diagnostics

    home = temporary / "home"
    codex_home = home / "codex"
    home.mkdir(parents=True, exist_ok=True)
    codex_home.mkdir()
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
    evidence = {"method": METHOD, "status": "failed", "failure": "not_started"}
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
            evidence = {"method": METHOD, "status": "initialize_failed"}
        else:
            _write_jsonrpc(process, {"method": "initialized", "params": {}})
            _write_jsonrpc(
                process, {"id": 2, "method": METHOD, "params": {}}
            )
            response = _read_jsonrpc_response(process, buffer, 2)
            result = response.get("result")
            valid = "error" not in response and valid_native_diagnostics(result)
            process_info = result.get("process") if isinstance(result, dict) else None
            gauges = result.get("gauges") if isinstance(result, dict) else None
            evidence = {
                "method": METHOD,
                "status": "passed" if valid else "invalid_response",
                "response_shape": {
                    "process_id_is_integer": isinstance(process_info, dict)
                    and type(process_info.get("id")) is int,
                    "gauges_is_list": isinstance(gauges, list),
                    "gauge_count": len(gauges) if isinstance(gauges, list) else None,
                },
            }
    except (OSError, RuntimeError, subprocess.SubprocessError, TimeoutError, ValueError) as error:
        evidence = {
            "method": METHOD,
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


def _write_json(path: Path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _copy_candidate_schemas(files, output: Path):
    destination = output / "candidate-schemas"
    destination.mkdir(parents=True, exist_ok=True)
    for _, path in files.values():
        shutil.copyfile(path, destination / path.name)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-tag", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--asset-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

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
        except (OSError, subprocess.SubprocessError) as error:
            files = {}
            generation_error = type(error).__name__

        baseline = json.loads(BASELINE.read_text())
        status = "unqualified"
        live_smoke = {"method": METHOD, "status": "not_run"}
        difference = {
            "missing_schemas": sorted(
                {"ServerDiagnosticsParams", "ServerDiagnosticsResponse"} - files.keys()
            ),
            "generation_error": generation_error,
        }
        if not difference["missing_schemas"]:
            _copy_candidate_schemas(files, args.output)
            candidate = {
                name: normalize(files[name][0])
                for name in ("ServerDiagnosticsParams", "ServerDiagnosticsResponse")
            }
            reviewed = {
                name: normalize(schema)
                for name, schema in baseline["schemas"].items()
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
                        args.binary, temporary_path / "smoke"
                    )
                except Exception as error:
                    live_smoke = {
                        "method": METHOD,
                        "status": "failed",
                        "failure": type(error).__name__,
                    }
                status = (
                    "qualified"
                    if live_smoke["status"] == "passed"
                    else "unqualified_live_protocol_smoke"
                )
            else:
                status = "needs_review"
            _write_json(
                args.output / "server-diagnostics-schema-diff.json", difference
            )

        binary_digest = sha256_file(args.binary)
        generated_hashes = {
            path.relative_to(generated).as_posix(): sha256_file(path)
            for _, path in files.values()
        }
        provenance = {
            "method": METHOD,
            "version": args.version,
            "source_tag": args.source_tag,
            "source_url": args.source_url,
            "source_asset_sha256": args.asset_sha256.removeprefix("sha256:"),
            "binary_sha256": binary_digest,
            "generated_files_sha256": generated_hashes,
            "baseline_version": baseline["native_version"],
            "qualification": status,
            "live_protocol_smoke": live_smoke["status"],
        }
        _write_json(args.output / "provenance.json", provenance)
        _write_json(args.output / "protocol-smoke.json", live_smoke)
        if status == "unqualified":
            _write_json(args.output / "server-diagnostics-schema-diff.json", difference)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
