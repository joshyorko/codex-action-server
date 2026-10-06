"""Exact MCP action catalog contract for implemented server profiles."""

import os

IMPLEMENTED_PROFILE = os.environ.get("CODEX_ACTION_PROFILE", "operator")

ACTION_NAMES_BY_PROFILE = {
    "operator": frozenset(
        {
            "add_thread_attachment",
            "add_thread_queue_item",
            "archive_thread",
            "call_mcp_tool",
            "clear_thread_goal",
            "compact_thread",
            "create_thread_and_start_turn",
            "create_thread_section",
            "delete_thread",
            "delete_thread_queue_item",
            "delete_thread_section",
            "discover_threads",
            "fork_thread",
            "get_thread_goal",
            "inject_thread_items",
            "inspect_target",
            "interrupt_turn",
            "list_apps",
            "list_background_terminals",
            "list_hooks",
            "list_loaded_threads",
            "list_mcp_server_status",
            "list_plugins",
            "list_skills",
            "list_models",
            "list_native_capabilities",
            "list_targets",
            "list_thread_attachments",
            "list_thread_items",
            "list_thread_queue",
            "list_thread_sections",
            "get_thread_snapshot",
            "list_thread_timeline",
            "list_thread_turns",
            "move_thread_to_section",
            "read_account_rate_limits",
            "read_account_usage",
            "read_app",
            "read_dispatch_receipt",
            "read_mcp_resource",
            "read_model_provider_capabilities",
            "read_plugin",
            "read_server_diagnostics",
            "read_thread",
            "reorder_thread_queue",
            "resume_thread",
            "revert_thread",
            "search_thread_occurrences",
            "search_threads",
            "set_thread_goal",
            "set_thread_name",
            "start_review",
            "start_thread",
            "start_thread_queue",
            "start_turn",
            "steer_turn",
            "remove_thread_attachment",
            "terminate_background_terminal",
            "unarchive_thread",
            "update_thread_section",
            "update_thread_metadata",
            "update_thread_queue_item",
            "update_thread_settings",
            "update_turn_settings",
        }
    ),
    "observe": frozenset(
        {
            "discover_threads",
            "get_thread_goal",
            "inspect_target",
            "list_apps",
            "list_background_terminals",
            "list_hooks",
            "list_loaded_threads",
            "list_mcp_server_status",
            "list_models",
            "list_native_capabilities",
            "list_plugins",
            "list_skills",
            "list_targets",
            "list_thread_attachments",
            "list_thread_items",
            "list_thread_queue",
            "list_thread_sections",
            "get_thread_snapshot",
            "list_thread_timeline",
            "list_thread_turns",
            "read_account_rate_limits",
            "read_account_usage",
            "read_app",
            "read_dispatch_receipt",
            "read_mcp_resource",
            "read_model_provider_capabilities",
            "read_plugin",
            "read_server_diagnostics",
            "read_thread",
            "search_thread_occurrences",
            "search_threads",
        }
    ),
}


def action_names_for_profile(profile: str | None = None) -> frozenset[str]:
    if profile is None:
        profile = IMPLEMENTED_PROFILE
    try:
        return ACTION_NAMES_BY_PROFILE[profile]
    except KeyError as error:
        raise ValueError(f"Unsupported action exposure profile: {profile}") from error


EXPECTED_ACTION_NAMES = action_names_for_profile()
