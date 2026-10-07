"""Package composition rejects ambiguous deployments before registration."""

from dataclasses import FrozenInstanceError
import importlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

import action_catalog_contract as contract
from test_mcp_annotations import READS, CONTROLS


def test_default_deployment_retains_compatibility_catalog(monkeypatch):
    monkeypatch.delenv("CODEX_ACTION_PACKAGES", raising=False)
    assert contract.selected_package_names() == ("codex-action-server",)
    assert contract.action_names_for_deployment("operator") == READS | CONTROLS


@pytest.mark.parametrize(
    "selection",
    [
        "",
        " ",
        ",",
        "codex-observe,",
        "unknown",
        "codex-observe,codex-observe",
        "codex-action-server,codex-observe",
        "codex-control,codex-action-server",
    ],
)
def test_invalid_package_selection_fails_closed(monkeypatch, selection):
    monkeypatch.setenv("CODEX_ACTION_PACKAGES", selection)
    with pytest.raises(ValueError, match="package"):
        contract.selected_package_names()


@pytest.mark.parametrize(
    "packages,expected",
    [
        (("codex-observe",), READS),
        (("codex-control",), CONTROLS),
        (("codex-control", "codex-observe"), READS | CONTROLS),
    ],
)
def test_operator_composes_disjoint_split_packages(packages, expected):
    assert contract.action_names_for_deployment("operator", packages) == expected


def test_observe_profile_rejects_explicit_control_selection(monkeypatch):
    monkeypatch.setenv("CODEX_ACTION_PACKAGES", "codex-observe,codex-control")
    with pytest.raises(ValueError, match="observe.*control"):
        contract.action_names_for_deployment("observe")
    assert (
        contract.action_names_for_deployment("observe", ("codex-action-server",))
        == READS
    )


@pytest.mark.parametrize(
    "packages",
    [
        (),
        ("unknown",),
        ("codex-observe", "codex-observe"),
        ("codex-action-server", "codex-control"),
    ],
)
def test_explicit_selection_has_the_same_guards(packages):
    with pytest.raises(ValueError, match="package"):
        contract.action_names_for_deployment("operator", packages)


def test_capability_records_preserve_composite_native_operations():
    assert contract.CAPABILITIES["get_thread_snapshot"].native_methods == {
        "thread/read",
        "thread/turns/list",
        "thread/items/list",
    }
    assert {
        "thread/start",
        "turn/start",
        "thread/resume",
        "thread/read",
    } == contract.CAPABILITIES["create_thread_and_start_turn"].native_methods
    assert contract.CAPABILITIES["list_targets"].native_methods == frozenset()
    with pytest.raises(FrozenInstanceError):
        contract.CAPABILITIES["list_targets"].name = "unsafe"
    with pytest.raises(TypeError):
        contract.CAPABILITIES["unsafe"] = contract.CAPABILITIES["list_targets"]


def test_control_only_native_projection_retains_guard_reads_but_not_unrelated_reads():
    methods = contract.native_methods_for_deployment("operator", ("codex-control",))
    assert {
        "thread/read",
        "thread/resume",
        "turn/start",
        "thread/backgroundTerminals/list",
        "threadSection/list",
    } <= methods
    assert "account/usage/read" not in methods


def test_registration_uses_metadata_and_denies_unselected_package():
    # Transport-double tests retain modules loaded with a fake actions decorator.
    # A fresh process verifies registration against the real Core hooks and errors.
    code = """
from actions import ActionError
from actions._hooks import on_action_func_found
from capability_registration import action
found = []

def list_targets():
    raise AssertionError("excluded action executed")

def interrupt_turn():
    return "controlled"

with on_action_func_found.register(lambda func, options: found.append((func.__name__, options))):
    denied = action(package="codex-observe")(list_targets)
    allowed = action(package="codex-control")(interrupt_turn)
assert [name for name, _ in found] == ["interrupt_turn"]
assert found[0][1]["is_consequential"] is True
assert allowed() == "controlled"
try:
    denied()
except ActionError as error:
    assert "unavailable" in str(error)
else:
    raise AssertionError("direct invocation escaped package selection")
"""
    root = Path(__file__).parents[1]
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env={
            **os.environ,
            "PYTHONPATH": str(root / "src"),
            "CODEX_ACTION_PROFILE": "operator",
            "CODEX_ACTION_PACKAGES": "codex-control",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_registration_rejects_action_in_wrong_package(monkeypatch):
    monkeypatch.delenv("CODEX_ACTION_PACKAGES", raising=False)
    registration = importlib.import_module("capability_registration")

    def list_targets():
        pass

    with pytest.raises(ValueError, match="package"):
        registration.action(package="codex-control")(list_targets)


def test_compatibility_native_projection_matches_reviewed_rpc_surface():
    from codex_rpc import METHODS

    assert (
        contract.native_methods_for_deployment("operator", ("codex-action-server",))
        == METHODS
    )


def test_selection_trims_whitespace_without_changing_requested_order(monkeypatch):
    monkeypatch.setenv("CODEX_ACTION_PACKAGES", " codex-control , codex-observe ")
    assert contract.selected_package_names() == ("codex-control", "codex-observe")
