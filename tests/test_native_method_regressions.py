"""Typed regression evidence must describe executed tests, not just exit code."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "native_method_regressions", ROOT / "ci/run_native_method_regressions.py"
)
regressions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(regressions)


def test_all_skipped_method_is_not_run_even_when_pytest_exits_successfully():
    assert (
        regressions._method_status(
            True,
            {"tests/test_contracts.py": {"passed": 0, "skipped": 3, "failed": 0}},
        )
        == "not_run"
    )


def test_mixed_pass_and_skip_records_coverage_and_qualifies_method():
    coverage = {
        "tests/test_contracts.py": {"passed": 2, "skipped": 1, "failed": 0},
        "tests/test_optional_runtime.py": {"passed": 0, "skipped": 4, "failed": 0},
    }
    assert regressions._method_status(True, coverage) == "passed"


def test_method_with_any_mapped_failure_fails_closed():
    coverage = {
        "tests/test_contracts.py": {"passed": 2, "skipped": 0, "failed": 0},
        "tests/test_optional_runtime.py": {"passed": 0, "skipped": 0, "failed": 1},
    }
    assert regressions._method_status(False, coverage) == "failed"


def test_malformed_outcome_counts_are_not_accepted():
    assert (
        regressions._method_status(
            True,
            {"tests/test_contracts.py": {"passed": True, "skipped": 0, "failed": 0}},
        )
        == "invalid_evidence"
    )


def test_collector_counts_only_test_call_outcomes():
    collector = regressions.RegressionOutcomeCollector(["tests/test_contracts.py"])
    collector.pytest_runtest_logreport(
        SimpleNamespace(
            nodeid="tests/test_contracts.py::test_skip",
            outcome="skipped",
            when="setup",
        )
    )
    collector.pytest_runtest_logreport(
        SimpleNamespace(
            nodeid="tests/test_contracts.py::test_pass",
            outcome="passed",
            when="call",
        )
    )
    assert collector.files["tests/test_contracts.py"] == {
        "passed": 1,
        "skipped": 1,
        "failed": 0,
    }
