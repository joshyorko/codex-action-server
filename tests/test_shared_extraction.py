"""Verify shared extraction against the independently retained pre-split source."""

import ast
import inspect
import os
from pathlib import Path
import subprocess
import sys

from test_actions import FakeClient, load_actions

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from extract_shared_implementation import definition_digest, bindings
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "src" / "codex_shared"
# Preserve the independently retained extraction baseline for every definition
# outside the reviewed execution-policy change. Policy behavior has dedicated
# native mapping, omission, observation and replay tests.
POLICY_DEFINITIONS = {
    "start_turn",
    "_optional_turn_policy",
    "_dispatch_run",
    "EffectiveConfiguration",
    "_optional_thread_settings",
    "ThreadSettingsFields",
    "TurnStartRequest",
    "_effective_configuration",
    "ExecutionPolicyFields",
}
BASELINE_DEFINITION_COUNT = 165
BASELINE_DEFINITION_SHA256 = (
    "8c1949bf3b9b597bf43b810c45e7b1d3c863e58a9bbddd9d231eb00d7fbaeb17"
)


def test_extraction_preserves_every_original_implementation_and_model_ast():
    definitions = []
    for path in SHARED.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if isinstance(
                node, (ast.FunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign)
            ):
                if set(bindings(node)).isdisjoint(POLICY_DEFINITIONS):
                    definitions.append(node)
    assert len(definitions) == BASELINE_DEFINITION_COUNT
    digest = definition_digest(definitions)
    assert digest == BASELINE_DEFINITION_SHA256


def test_compatibility_wrappers_preserve_signatures_and_delegate_once():
    module = load_actions()
    wrappers = ast.parse((ROOT / "src" / "codex_actions.py").read_text())
    endpoints = [node for node in wrappers.body if isinstance(node, ast.FunctionDef)]
    assert len(endpoints) == 64
    for node in endpoints:
        assert len(node.decorator_list) == 1
        assert (
            ast.unparse(node.decorator_list[0])
            == "action(package='codex-action-server')"
        )
        assert len(node.body) == 2
        assert isinstance(node.body[1], ast.Return)
        call = node.body[1].value
        assert isinstance(call, ast.Call)
        assert isinstance(call.func, ast.Attribute)
        assert call.func.attr == node.name
        implementation = getattr(getattr(module.public, call.func.value.id), node.name)
        wrapper = getattr(module.public, node.name)
        assert inspect.signature(wrapper) == inspect.signature(implementation)
        assert wrapper.__doc__ == implementation.__doc__
        assert implementation.__module__.startswith("codex_shared.")
        assert wrapper.__module__ == "codex_actions"


def test_real_shared_imports_register_nothing_and_entrypoints_register_all():
    import capability_registration

    code = """
import importlib
import sys
sys.path.insert(0, sys.argv[1])
import capability_registration
sys.path.insert(0, sys.argv[2])
from actions._hooks import on_action_func_found
registered = []
with on_action_func_found.register(lambda func, options: registered.append(func)):
    for group in ('models', 'common', 'inspection', 'threads', 'execution', 'organization', 'queues', 'integrations'):
        importlib.import_module('codex_shared.' + group)
    assert registered == [], registered
    import codex_actions
assert len(registered) == 64, len(registered)
assert len({function.__name__ for function in registered}) == 64
assert all(function.__module__ == 'codex_actions' for function in registered)
assert all(function.__code__.co_filename == sys.argv[2] + '/codex_actions.py' for function in registered)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(Path(capability_registration.__file__).parent),
            str(ROOT / "src"),
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "CODEX_ACTION_PROFILE": "operator",
            "CODEX_ACTION_PACKAGES": "codex-action-server",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_fake_loader_reloads_real_framework_classes_and_patches_shared_owner():
    code = """
import codex_actions
from test_actions import load_actions, FakeClient
from unittest.mock import patch
module = load_actions()
assert module.public.read_thread.__module__ == 'codex_actions'
client = FakeClient(None)
from codex_shared import common
original = common.Client
with patch.object(module, 'Client', return_value=client), patch.object(module, 'resolve_target', return_value='local'):
    assert common.Client is module.Client
    response = module.read_thread(module.ThreadReadRequest(target='local', cwd='/trusted', thread_id='thread-1'))
assert common.Client is original
assert type(response).__name__ == 'FakeResponse'
assert client.calls == [('thread/read', {'threadId': 'thread-1', 'includeTurns': False})]
"""
    # Registration can be supplied by an independent worktree while implementing
    # this lane, but the exercised wrapper and transport owners must be ours.
    import capability_registration

    bootstrap = "import sys; sys.path.insert(0, sys.argv[1]); import capability_registration; sys.path.insert(0, sys.argv[2]); sys.path.insert(0, sys.argv[3]);\n"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            bootstrap + code,
            str(Path(capability_registration.__file__).parent),
            str(ROOT / "src"),
            str(ROOT / "tests"),
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "CODEX_ACTION_PROFILE": "operator",
            "CODEX_ACTION_PACKAGES": "codex-action-server",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_patch_adapter_keeps_real_public_wrapper_and_restores_shared_globals():
    module = load_actions()
    client = FakeClient(None)
    from codex_shared import common

    original_client = common.Client
    original_target = common.resolve_target
    with (
        patch.object(module, "Client", return_value=client),
        patch.object(module, "resolve_target", return_value="local"),
    ):
        response = module.public.read_thread(
            module.ThreadReadRequest(
                target="local", cwd="/trusted", thread_id="thread-1"
            )
        )
    assert common.Client is original_client
    assert common.resolve_target is original_target
    assert response.result.result["thread"]["id"] == "thread-1"
    assert client.calls == [
        ("thread/read", {"threadId": "thread-1", "includeTurns": False})
    ]
