#!/usr/bin/env python3
"""RCC tasks that reuse the repository's native qualification pipeline."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def run(command: list[str]) -> int:
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def main() -> int:
    if len(sys.argv) != 2:
        print("expected one RCC task name", file=sys.stderr)
        return 2
    task = sys.argv[1]
    python = sys.executable
    if task == "check-catalog":
        catalog = run(
            [python, str(ROOT / "ci/generate_native_method_catalog.py"), "--check"]
        )
        if catalog:
            return catalog
        return run(
            [python, str(ROOT / "ci/generate_native_qualification.py"), "--check"]
        )
    if task == "native-regressions":
        version = os.environ.get("CODEX_CANDIDATE_VERSION", "")
        evidence = os.environ.get("CODEX_REGRESSION_EVIDENCE", "")
        if not version or not evidence:
            print("candidate version and evidence output are required", file=sys.stderr)
            return 2
        env = {**os.environ, "TMPDIR": "/tmp"}
        return subprocess.run(
            [
                python,
                str(ROOT / "ci/run_native_method_regressions.py"),
                "--candidate-version",
                version,
                "--output",
                evidence,
            ],
            cwd=ROOT,
            env=env,
            check=False,
        ).returncode
    if task == "focused-tests":
        return run(
            [
                python,
                "-m",
                "pytest",
                "-q",
                "tests/test_codex_qualification.py",
                "tests/test_native_method_catalog.py",
                "tests/test_native_qualification_receipt.py",
                "tests/test_image_publication_workflow.py",
                "tests/test_renovate_config.py",
            ]
        )
    if task == "lint":
        lint = run(
            [
                python,
                "-m",
                "ruff",
                "check",
                "ci/generate_native_method_catalog.py",
                "ci/run_native_method_regressions.py",
                "ci/qualify_codex_schema.py",
                "ci/propose_codex_qualification.py",
                "ci/generate_native_qualification.py",
                "ci/report_codex_qualification.py",
                "ci/create_native_qualification_receipt.py",
                "ci/codex_qualification/tasks.py",
                "tests/test_native_method_catalog.py",
                "tests/test_codex_qualification.py",
                "tests/test_native_qualification_receipt.py",
                "tests/test_image_publication_workflow.py",
                "tests/test_renovate_config.py",
            ]
        )
        if lint:
            return lint
        return run(
            [
                python,
                "-m",
                "ruff",
                "format",
                "--check",
                "ci/generate_native_method_catalog.py",
                "ci/run_native_method_regressions.py",
                "ci/qualify_codex_schema.py",
                "ci/propose_codex_qualification.py",
                "ci/generate_native_qualification.py",
                "ci/report_codex_qualification.py",
                "ci/create_native_qualification_receipt.py",
                "ci/codex_qualification/tasks.py",
                "tests/test_native_method_catalog.py",
                "tests/test_codex_qualification.py",
                "tests/test_native_qualification_receipt.py",
                "tests/test_image_publication_workflow.py",
                "tests/test_renovate_config.py",
            ]
        )
    if task == "format":
        return run(
            [
                python,
                "-m",
                "ruff",
                "format",
                "ci/generate_native_method_catalog.py",
                "ci/run_native_method_regressions.py",
                "ci/qualify_codex_schema.py",
                "ci/propose_codex_qualification.py",
                "ci/generate_native_qualification.py",
                "ci/report_codex_qualification.py",
                "ci/create_native_qualification_receipt.py",
                "ci/codex_qualification/tasks.py",
                "tests/test_native_method_catalog.py",
                "tests/test_codex_qualification.py",
                "tests/test_native_qualification_receipt.py",
                "tests/test_image_publication_workflow.py",
                "tests/test_renovate_config.py",
            ]
        )
    print(f"unknown RCC task: {task}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
