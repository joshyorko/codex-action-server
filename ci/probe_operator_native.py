#!/usr/bin/env python3
"""Run experimental native positives in an isolated loopback-only fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess

from qualify_codex_schema import schema_files
from qualify_native_methods import qualify


def deadline(_signum, _frame):
    raise TimeoutError("operator qualification deadline exceeded")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--schemas", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    reported = subprocess.run(
        [str(args.binary), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        env={
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(args.output.parent),
            "CODEX_HOME": str(args.output.parent / "version-home"),
        },
    ).stdout.strip()
    if reported != f"codex-cli {args.version}":
        raise ValueError("native binary does not match pinned candidate version")
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(150)
    try:
        result = qualify(
            args.binary,
            args.schemas,
            args.output.parent,
            version=args.version,
            local_provider=True,
        )
    finally:
        signal.alarm(0)
    schemas = schema_files(args.schemas)
    dependencies = {
        relative: hashlib.file_digest(
            (args.repo / relative).open("rb"), "sha256"
        ).hexdigest()
        for relative in ("ci/qualify_native_methods.py", "ci/qualify_codex_schema.py")
    }
    methods = {}
    catalog = json.loads(
        (args.repo / "ci/codex_qualification/native_methods.json").read_text()
    )
    for row in catalog["methods"]:
        proof = result["methods"].get(row["method"])
        if proof is None:
            continue
        methods[row["method"]] = {
            **proof["protocol_smoke"],
            "request_schema": row["request_schema"],
            "response_schema": row["response_schema"],
            "fixture_dependencies_sha256": dependencies,
            **{
                f"{kind}_sha256": hashlib.file_digest(
                    schemas[row[f"{kind}_schema"]][1].open("rb"), "sha256"
                ).hexdigest()
                for kind in ("request", "response")
            },
        }
    result["methods"] = methods
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
