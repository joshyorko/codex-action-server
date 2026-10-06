"""Operator-owned action profiles must agree with registration and execution."""

import os
from pathlib import Path
import subprocess
import sys

from action_catalog_contract import ACTION_NAMES_BY_PROFILE


ROOT = Path(__file__).parents[1]


def run_profile(profile: str) -> subprocess.CompletedProcess[str]:
    code = """
import os
import sys
sys.path.insert(0, "src")
from actions import ActionError
from actions._hooks import on_action_func_found
from action_catalog_contract import ACTION_NAMES_BY_PROFILE
registered = []
with on_action_func_found.register(lambda func, options: registered.append(func.__name__)):
    import codex_actions
from action_catalog_contract import action_names_for_profile
assert set(registered) == action_names_for_profile()
from native_capabilities import inventory
catalog = inventory("codex-cli 0.160.1")
assert catalog["server_exposure_profile"] == os.environ["CODEX_ACTION_PROFILE"]
if action_names_for_profile() == action_names_for_profile("observe"):
    assert catalog["server_exposure_profile"] == "observe"
    control = next(f for f in catalog["families"] if f["classification"] == "OPERATOR_CONTROL")
    assert control["exposed_methods"] == []
    experimental = next(f for f in catalog["families"] if f["classification"] == "EXPERIMENTAL")
    assert "app/read" in experimental["exposed_methods"]
    assert "thread/queue/start" not in experimental["exposed_methods"]
    for name in ACTION_NAMES_BY_PROFILE["operator"] - action_names_for_profile():
        try:
            getattr(codex_actions, name)({"profile": "operator"})
        except ActionError as error:
            assert "unavailable" in str(error)
        else:
            raise AssertionError(f"direct invocation escaped the observe profile: {name}")
"""
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={**os.environ, "CODEX_ACTION_PROFILE": profile},
        capture_output=True,
        text=True,
        check=False,
    )


def test_observe_profile_registers_only_explicit_reads_and_denies_direct_mutation():
    result = run_profile("observe")

    assert result.returncode == 0, result.stderr
    assert ACTION_NAMES_BY_PROFILE["observe"] < ACTION_NAMES_BY_PROFILE["operator"]


def test_operator_profile_preserves_full_catalog():
    result = run_profile("operator")

    assert result.returncode == 0, result.stderr


def test_unknown_profile_fails_closed_on_import():
    result = run_profile("admin")

    assert result.returncode != 0
    assert "Unsupported action exposure profile" in result.stderr
