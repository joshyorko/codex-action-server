#!/usr/bin/env python3
"""Project CAS tool policy through the exact PyPI runtime MCP adapter."""

import ast
import hashlib
import inspect
from importlib.metadata import distribution, version


RUNTIME_VERSION = "1.0.2"
SDK_VERSION = "2.0.0"
SOURCE_SHA256 = "d8d8cf0914419c3e2037b81339d089b3cbdc1900f7dd3027dc8751d19ac73898"

READ_ONLY_TOOLS = frozenset(
    {
        "read_dispatch_receipt",
        "list_targets",
        "list_native_capabilities",
        "inspect_target",
        "discover_threads",
        "list_thread_turns",
        "list_thread_items",
        "list_thread_sections",
        "search_threads",
        "search_thread_occurrences",
        "list_thread_timeline",
        "read_thread",
        "get_thread_goal",
        "list_models",
        "read_model_provider_capabilities",
        "read_server_diagnostics",
        "list_mcp_server_status",
        "list_loaded_threads",
    }
)
CONTROL_TOOLS = frozenset(
    {
        "fork_thread",
        "archive_thread",
        "unarchive_thread",
        "delete_thread",
        "set_thread_name",
        "update_thread_metadata",
        "revert_thread",
        "compact_thread",
        "update_thread_settings",
        "update_turn_settings",
        "set_thread_goal",
        "clear_thread_goal",
        "start_thread",
        "create_thread_and_start_turn",
        "resume_thread",
        "start_turn",
        "steer_turn",
        "interrupt_turn",
    }
)

ORIGINAL = "        options = json.loads(action.options) if action.options else {}\n"
REPLACEMENT = (
    ORIGINAL
    + """        options = annotation_options(
            action_package.name, action.file, action.name, options
        )
"""
)


def annotation_options(package_name, file, name, options):
    if (
        package_name != "codex-action-server"
        or file != "src/codex_actions.py"
        or options.get("kind", "action") != "action"
    ):
        return options
    projected = dict(options)
    read_only = name in READ_ONLY_TOOLS
    projected.update(read_only_hint=read_only, destructive_hint=not read_only)
    return projected


def patch_source(source, runtime_version, sdk_version):
    if runtime_version != RUNTIME_VERSION or sdk_version != SDK_VERSION:
        raise RuntimeError("unsupported_runtime_or_sdk_version")
    if hashlib.sha256(source.encode()).hexdigest() != SOURCE_SHA256:
        raise RuntimeError("unexpected_runtime_adapter_source")
    if source.count(ORIGINAL) != 1:
        raise RuntimeError("unexpected_runtime_adapter_shape")
    policy = (
        f"\n\nREAD_ONLY_TOOLS = frozenset({tuple(sorted(READ_ONLY_TOOLS))!r})\n"
        f"CONTROL_TOOLS = frozenset({tuple(sorted(CONTROL_TOOLS))!r})\n\n"
        + inspect.getsource(annotation_options)
    )
    patched = source.replace(ORIGINAL, REPLACEMENT) + policy
    ast.parse(patched)
    return patched


def main():
    runtime = distribution("actions-runtime")
    path = runtime.locate_file("actions/server/mcp/setup_mcp_server_v2.py")
    patched = patch_source(path.read_text(), runtime.version, version("mcp"))
    path.write_text(patched)
    print("Installed scoped CAS MCP annotations for actions-runtime 1.0.2")


if __name__ == "__main__":
    main()
