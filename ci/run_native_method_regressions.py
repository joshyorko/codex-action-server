#!/usr/bin/env python3
"""Run the typed regression tests mapped to every CAS-consumed native RPC."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "ci/codex_qualification/native_methods.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def regression_evidence(candidate_version: str) -> dict:
    catalog = json.loads(CATALOG.read_text())
    if catalog.get("schema_version") != 1 or not isinstance(
        catalog.get("methods"), list
    ):
        raise ValueError("native method catalog is malformed")

    methods = {}
    files = set()
    for row in catalog["methods"]:
        method = row.get("method")
        test_files = row.get("regression_tests")
        if (
            not isinstance(method, str)
            or not isinstance(test_files, list)
            or not test_files
        ):
            raise ValueError("every native method needs typed regression test paths")
        validated = []
        for name in test_files:
            if not isinstance(name, str) or not name.startswith("tests/"):
                raise ValueError(f"invalid regression test path for {method}")
            path = (ROOT / name).resolve()
            if ROOT not in path.parents or not path.is_file():
                raise ValueError(f"regression test path is missing for {method}")
            validated.append(name)
            files.add(name)
        methods[method] = sorted(set(validated))

    return {
        "schema_version": 1,
        "candidate_version": candidate_version,
        "status": "not_run",
        "test_files": {name: _sha256(ROOT / name) for name in sorted(files)},
        "methods": {
            method: {"status": "not_run", "test_files": paths}
            for method, paths in sorted(methods.items())
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    evidence = regression_evidence(args.candidate_version)
    paths = sorted(evidence["test_files"])
    sys.path.insert(0, str(ROOT / "src"))
    import pytest

    result = pytest.main(["-q", "-rs", *paths])
    status = "passed" if result == pytest.ExitCode.OK else "failed"
    evidence["status"] = status
    evidence["pytest_exit_code"] = int(result)
    for row in evidence["methods"].values():
        row["status"] = status
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
