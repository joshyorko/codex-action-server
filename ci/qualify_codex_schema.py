#!/usr/bin/env python3
"""Generate and compare a pinned Codex candidate's diagnostics schema."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

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
        generated.mkdir()
        environment = {"PATH": os.environ.get("PATH", ""), "CODEX_HOME": str(home)}
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
        difference = {
            "missing_schemas": sorted(
                {"ServerDiagnosticsParams", "ServerDiagnosticsResponse"} - files.keys()
            ),
            "generation_error": generation_error,
        }
        if not difference["missing_schemas"]:
            candidate = {
                name: normalize(files[name][0])
                for name in ("ServerDiagnosticsParams", "ServerDiagnosticsResponse")
            }
            reviewed = {name: normalize(schema) for name, schema in baseline["schemas"].items()}
            difference = {
                "baseline": reviewed,
                "candidate": candidate,
                "identical": candidate == reviewed,
            }
            status = (
                "schema_unchanged_requires_live_protocol_smoke"
                if candidate == reviewed
                else "needs_review"
            )
            (args.output / "server-diagnostics-schema-diff.json").write_text(
                json.dumps(difference, indent=2, sort_keys=True) + "\n"
            )
        binary_digest = sha256_file(args.binary)
        provenance = {
            "method": METHOD,
            "version": args.version,
            "source_tag": args.source_tag,
            "source_url": args.source_url,
            "source_asset_sha256": args.asset_sha256.removeprefix("sha256:"),
            "binary_sha256": binary_digest,
            "generated_files_sha256": {
                path.relative_to(generated).as_posix(): sha256_file(path)
                for _, path in files.values()
            },
            "baseline_version": baseline["native_version"],
            "qualification": status,
            "live_protocol_smoke": "not_run",
        }
        (args.output / "provenance.json").write_text(
            json.dumps(provenance, indent=2, sort_keys=True) + "\n"
        )
        if status == "unqualified":
            (args.output / "server-diagnostics-schema-diff.json").write_text(
                json.dumps(difference, indent=2, sort_keys=True) + "\n"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
