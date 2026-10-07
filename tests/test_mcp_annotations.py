"""Every Codex action has an explicit behavioral MCP classification."""

from pathlib import Path
import runpy
import json
import os
import subprocess
import sys

import pytest

from action_catalog_contract import EXPECTED_ACTION_NAMES


ROOT = Path(__file__).parents[1]
POLICY = runpy.run_path(str(ROOT / "scripts/install_runtime_annotation_patch.py"))
READS = {
    "list_targets",
    "list_native_capabilities",
    "inspect_target",
    "discover_threads",
    "read_thread",
    "list_thread_turns",
    "list_thread_items",
    "list_thread_sections",
    "get_thread_snapshot",
    "search_threads",
    "search_thread_occurrences",
    "list_thread_timeline",
    "list_thread_queue",
    "read_account_rate_limits",
    "read_account_usage",
    "read_app",
    "list_skills",
    "list_hooks",
    "list_plugins",
    "read_plugin",
    "list_apps",
    "read_app",
    "read_mcp_resource",
    "list_background_terminals",
    "list_thread_attachments",
    "list_loaded_threads",
    "list_models",
    "list_mcp_server_status",
    "read_server_diagnostics",
    "read_model_provider_capabilities",
    "get_thread_goal",
    "read_dispatch_receipt",
}
CONTROLS = {
    "fork_thread",
    "archive_thread",
    "unarchive_thread",
    "delete_thread",
    "set_thread_name",
    "update_thread_metadata",
    "revert_thread",
    "compact_thread",
    "add_thread_queue_item",
    "update_thread_queue_item",
    "delete_thread_queue_item",
    "reorder_thread_queue",
    "start_thread_queue",
    "start_review",
    "call_mcp_tool",
    "terminate_background_terminal",
    "add_thread_attachment",
    "remove_thread_attachment",
    "inject_thread_items",
    "create_thread_section",
    "update_thread_section",
    "delete_thread_section",
    "move_thread_to_section",
    "move_thread_to_section",
    "start_thread",
    "create_thread_and_start_turn",
    "resume_thread",
    "start_turn",
    "steer_turn",
    "interrupt_turn",
    "update_thread_settings",
    "update_turn_settings",
    "set_thread_goal",
    "clear_thread_goal",
}


def test_complete_actual_catalog_is_intentionally_classified():
    code = """
import json
from actions._hooks import on_action_func_found
registered = {}
with on_action_func_found.register(lambda func, options: registered.update({func.__name__: options["is_consequential"]})):
    import codex_actions
print(json.dumps(registered))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "CODEX_ACTION_PROFILE": "operator",
            "CODEX_ACTION_PACKAGES": "codex-action-server",
        },
        text=True,
        capture_output=True,
        check=True,
    )
    declared = json.loads(result.stdout)
    assert set(declared) == READS | CONTROLS
    assert set(declared) == EXPECTED_ACTION_NAMES
    assert POLICY["READ_ONLY_TOOLS"] == READS
    assert POLICY["CONTROL_TOOLS"] == CONTROLS
    assert not READS & CONTROLS
    assert {
        name for name, consequential in declared.items() if not consequential
    } == READS


@pytest.mark.parametrize("name", sorted(READS | CONTROLS))
def test_policy_preserves_action_metadata_and_classifies_every_tool(name):
    original = {
        "kind": "action",
        "is_consequential": name in CONTROLS,
        "title": "Preserve title",
        "_meta": {"fixture": "preserve"},
        "open_world_hint": True,
        "idempotent_hint": name in CONTROLS,
    }
    projected = POLICY["annotation_options"](
        "codex-action-server", "src/codex_actions.py", name, original
    )
    assert projected["read_only_hint"] is (name in READS)
    assert projected["destructive_hint"] is (name in CONTROLS)
    assert projected["idempotent_hint"] is (name in READS)
    assert projected.get("open_world_hint", True) is True
    projected_metadata = {
        k: v
        for k, v in projected.items()
        if k not in {"read_only_hint", "destructive_hint", "idempotent_hint"}
    }
    original_metadata = {k: v for k, v in original.items() if k != "idempotent_hint"}
    assert projected_metadata == original_metadata
    assert "read_only_hint" not in original


def test_unknown_codex_action_remains_conservative():
    projected = POLICY["annotation_options"](
        "codex-action-server",
        "src/codex_actions.py",
        "future_worker_lifecycle",
        {
            "read_only_hint": True,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    assert projected["read_only_hint"] is False
    assert projected["destructive_hint"] is True
    assert projected["idempotent_hint"] is False
    assert projected.get("open_world_hint", True) is True


@pytest.mark.parametrize(
    "package,file,options",
    [
        ("foreign-package", "src/codex_actions.py", {}),
        ("codex-action-server", "foreign.py", {}),
        ("codex-action-server", "src/codex_actions.py", {"kind": "resource"}),
    ],
)
def test_foreign_packages_files_and_non_actions_are_unchanged(package, file, options):
    assert (
        POLICY["annotation_options"](package, file, "list_targets", options) == options
    )


@pytest.mark.parametrize("name", ["list_targets", "discover_threads"])
def test_executor_destructive_tool_rule_does_not_gate_read(name):
    hints = POLICY["annotation_options"](
        "codex-action-server", "src/codex_actions.py", name, {}
    )
    assert (hints["destructive_hint"] is True) is False
    assert hints["read_only_hint"] is True


@pytest.mark.parametrize("runtime,sdk", [("1.0.3", "2.0.0"), ("1.0.2", "2.0.1")])
def test_projection_patch_rejects_unreviewed_versions(runtime, sdk):
    with pytest.raises(RuntimeError):
        POLICY["patch_source"]("untrusted source", runtime, sdk)


def test_projection_patch_rejects_changed_upstream_source():
    with pytest.raises(RuntimeError):
        POLICY["patch_source"]("untrusted source", "1.0.2", "2.0.0")


@pytest.mark.parametrize(
    "package,names", [("codex-observe", READS), ("codex-control", CONTROLS)]
)
def test_split_package_actions_receive_the_same_reviewed_hints(package, names):
    for name in names:
        projected = POLICY["annotation_options"](
            package, "src/codex_actions.py", name, {}
        )
        assert projected["read_only_hint"] is (name in READS)
        assert projected["destructive_hint"] is (name in CONTROLS)
        assert projected["idempotent_hint"] is (name in READS)


def test_other_split_package_names_never_receive_read_hints():
    projected = POLICY["annotation_options"](
        "codex-control", "src/codex_actions.py", "list_targets", {}
    )
    assert projected["read_only_hint"] is False
    assert projected["destructive_hint"] is True


def test_serialized_patch_policy_is_independent_of_project_runtime_imports():
    import hashlib

    source = "def register():\n" + POLICY["ORIGINAL"]
    patch = POLICY["patch_source"]
    previous = patch.__globals__["SOURCE_SHA256"]
    patch.__globals__["SOURCE_SHA256"] = hashlib.sha256(source.encode()).hexdigest()
    try:
        patched = patch(source, "1.0.2", "2.0.0")
    finally:
        patch.__globals__["SOURCE_SHA256"] = previous
    # Only the adapter replacement is nested in real runtime code; execute the serialized suffix independently.
    policy = patched.split("\n\nREVIEWED_ANNOTATIONS =", 1)[1]
    namespace = {}
    exec("REVIEWED_ANNOTATIONS =" + policy, namespace)
    assert (
        namespace["annotation_options"](
            "codex-observe", "src/codex_actions.py", "list_targets", {}
        )["read_only_hint"]
        is True
    )
    assert (
        namespace["annotation_options"](
            "foreign-package", "src/codex_actions.py", "list_targets", {}
        )
        == {}
    )
