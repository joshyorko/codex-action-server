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
OUTCOMES = ("passed", "skipped", "failed")


class RegressionOutcomeCollector:
    """Count actual pytest outcomes for each catalogued regression file."""

    def __init__(self, paths: list[str]):
        self.files = {name: {outcome: 0 for outcome in OUTCOMES} for name in paths}

    def pytest_runtest_logreport(self, report):
        name = report.nodeid.split("::", 1)[0].replace("\\", "/")
        counts = self.files.get(name)
        if counts is None:
            return
        if report.outcome == "failed":
            counts["failed"] += 1
        elif report.outcome == "skipped":
            counts["skipped"] += 1
        elif report.when == "call" and report.outcome == "passed":
            counts["passed"] += 1


def _method_status(pytest_succeeded: bool, coverage: dict[str, dict[str, int]]) -> str:
    if not isinstance(coverage, dict) or not coverage:
        return "not_run"
    for counts in coverage.values():
        if (
            not isinstance(counts, dict)
            or set(counts) != set(OUTCOMES)
            or any(
                type(counts.get(key)) is not int or counts[key] < 0 for key in OUTCOMES
            )
        ):
            return "invalid_evidence"
        if counts["failed"]:
            return "failed"
    if not pytest_succeeded:
        return "failed"
    if not any(counts["passed"] for counts in coverage.values()):
        return "not_run"
    return "passed"


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

    outcomes = RegressionOutcomeCollector(paths)
    result = pytest.main(["-q", "-rs", *paths], plugins=[outcomes])
    pytest_succeeded = result == pytest.ExitCode.OK
    has_passed_tests = any(row["passed"] for row in outcomes.files.values())
    status = (
        "failed"
        if result != pytest.ExitCode.OK
        else "passed"
        if has_passed_tests
        else "not_run"
    )
    evidence["status"] = status
    evidence["pytest_exit_code"] = int(result)
    evidence["test_outcomes"] = outcomes.files
    for row in evidence["methods"].values():
        row["coverage"] = {name: outcomes.files[name] for name in row["test_files"]}
        row["status"] = _method_status(pytest_succeeded, row["coverage"])
        if status != "passed" and row["status"] == "passed":
            row["status"] = status
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
