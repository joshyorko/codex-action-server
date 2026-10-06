"""Every Codex action has an explicit behavioral MCP classification."""

import ast
from pathlib import Path
import runpy

import pytest


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
    "search_threads",
    "search_thread_occurrences",
    "list_thread_timeline",
    "list_thread_queue",
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
    declared = {}
    for node in ast.parse((ROOT / "src/codex_actions.py").read_text()).body:
        if isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Name)
                    and decorator.func.id == "action"
                ):
                    declared[node.name] = next(
                        ast.literal_eval(k.value)
                        for k in decorator.keywords
                        if k.arg == "is_consequential"
                    )
    assert set(declared) == READS | CONTROLS
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
    }
    projected = POLICY["annotation_options"](
        "codex-action-server", "src/codex_actions.py", name, original
    )
    assert projected["read_only_hint"] is (name in READS)
    assert projected["destructive_hint"] is (name in CONTROLS)
    assert {
        k: v
        for k, v in projected.items()
        if k not in {"read_only_hint", "destructive_hint"}
    } == original
    assert "read_only_hint" not in original


def test_unknown_codex_action_remains_conservative():
    projected = POLICY["annotation_options"](
        "codex-action-server",
        "src/codex_actions.py",
        "future_worker_lifecycle",
        {"read_only_hint": True, "destructive_hint": False},
    )
    assert projected["read_only_hint"] is False
    assert projected["destructive_hint"] is True


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
