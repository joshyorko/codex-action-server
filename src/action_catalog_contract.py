"""Source-owned capabilities and exact package/profile deployment contracts."""

from dataclasses import dataclass
import os
from types import MappingProxyType

IMPLEMENTED_PROFILE = os.environ.get("CODEX_ACTION_PROFILE", "operator")
COMPATIBILITY_PACKAGE = "codex-action-server"
OBSERVE_PACKAGE = "codex-observe"
CONTROL_PACKAGE = "codex-control"


@dataclass(frozen=True)
class Capability:
    name: str
    group: str
    packages: frozenset[str]
    profiles: frozenset[str]
    is_consequential: bool
    read_only_hint: bool
    destructive_hint: bool
    idempotent_hint: bool
    open_world_hint: bool
    native_methods: frozenset[str]


def _capability(
    name: str, group: str, consequential: bool, *methods: str
) -> Capability:
    return Capability(
        name=name,
        group=group,
        packages=frozenset(
            {
                COMPATIBILITY_PACKAGE,
                CONTROL_PACKAGE if consequential else OBSERVE_PACKAGE,
            }
        ),
        profiles=frozenset({"operator"} if consequential else {"operator", "observe"}),
        is_consequential=consequential,
        read_only_hint=not consequential,
        destructive_hint=consequential,
        idempotent_hint=not consequential,
        open_world_hint=True,
        native_methods=frozenset(methods),
    )


# Native mappings include guard reads, optional settings, and composite operations.
_CAPABILITY_RECORDS = (
    _capability("read_dispatch_receipt", "inspection", False),
    _capability("list_targets", "inspection", False),
    _capability("inspect_target", "inspection", False),
    _capability("list_native_capabilities", "inspection", False),
    _capability("discover_threads", "threads", False, "thread/list"),
    _capability("fork_thread", "organization", True, "thread/fork", "thread/read"),
    _capability(
        "archive_thread", "organization", True, "thread/archive", "thread/read"
    ),
    _capability(
        "unarchive_thread", "organization", True, "thread/read", "thread/unarchive"
    ),
    _capability("delete_thread", "organization", True, "thread/delete", "thread/read"),
    _capability(
        "set_thread_name", "organization", True, "thread/name/set", "thread/read"
    ),
    _capability(
        "update_thread_metadata",
        "organization",
        True,
        "thread/metadata/update",
        "thread/read",
    ),
    _capability("revert_thread", "organization", True, "thread/read", "thread/revert"),
    _capability(
        "compact_thread", "organization", True, "thread/compact/start", "thread/read"
    ),
    _capability("start_review", "execution", True, "review/start", "thread/read"),
    _capability(
        "read_account_rate_limits", "inspection", False, "account/rateLimits/read"
    ),
    _capability(
        "read_account_usage", "inspection", False, "account/usage/read", "thread/read"
    ),
    _capability("list_skills", "integrations", False, "skills/list"),
    _capability("list_hooks", "integrations", False, "hooks/list"),
    _capability("list_plugins", "integrations", False, "plugin/list"),
    _capability("read_plugin", "integrations", False, "plugin/read"),
    _capability("list_apps", "integrations", False, "app/list", "thread/read"),
    _capability("read_app", "integrations", False, "app/read", "thread/read"),
    _capability(
        "read_mcp_resource",
        "integrations",
        False,
        "mcpServer/resource/read",
        "thread/read",
    ),
    _capability(
        "call_mcp_tool", "integrations", True, "mcpServer/tool/call", "thread/read"
    ),
    _capability(
        "list_background_terminals",
        "organization",
        False,
        "thread/backgroundTerminals/list",
        "thread/read",
    ),
    _capability(
        "terminate_background_terminal",
        "organization",
        True,
        "thread/backgroundTerminals/list",
        "thread/backgroundTerminals/terminate",
        "thread/read",
    ),
    _capability(
        "list_thread_attachments",
        "organization",
        False,
        "thread/attachment/list",
        "thread/read",
    ),
    _capability(
        "add_thread_attachment",
        "organization",
        True,
        "thread/attachment/add",
        "thread/read",
    ),
    _capability(
        "remove_thread_attachment",
        "organization",
        True,
        "thread/attachment/remove",
        "thread/read",
    ),
    _capability(
        "inject_thread_items",
        "organization",
        True,
        "thread/inject_items",
        "thread/read",
    ),
    _capability("list_thread_sections", "threads", False, "threadSection/list"),
    _capability("search_threads", "threads", False, "thread/search"),
    _capability(
        "search_thread_occurrences",
        "threads",
        False,
        "thread/read",
        "thread/searchOccurrences",
    ),
    _capability(
        "list_thread_timeline", "threads", False, "thread/read", "thread/timeline/list"
    ),
    _capability(
        "list_thread_queue", "queues", False, "thread/queue/list", "thread/read"
    ),
    _capability(
        "add_thread_queue_item", "queues", True, "thread/queue/add", "thread/read"
    ),
    _capability(
        "update_thread_queue_item", "queues", True, "thread/queue/update", "thread/read"
    ),
    _capability(
        "delete_thread_queue_item", "queues", True, "thread/queue/delete", "thread/read"
    ),
    _capability(
        "reorder_thread_queue", "queues", True, "thread/queue/reorder", "thread/read"
    ),
    _capability(
        "start_thread_queue", "queues", True, "thread/queue/start", "thread/read"
    ),
    _capability("create_thread_section", "organization", True, "threadSection/create"),
    _capability("update_thread_section", "organization", True, "threadSection/update"),
    _capability("delete_thread_section", "organization", True, "threadSection/delete"),
    _capability(
        "move_thread_to_section",
        "organization",
        True,
        "thread/read",
        "thread/section/move",
        "threadSection/list",
    ),
    _capability(
        "list_thread_turns", "threads", False, "thread/read", "thread/turns/list"
    ),
    _capability(
        "list_thread_items", "threads", False, "thread/items/list", "thread/read"
    ),
    _capability("read_thread", "threads", False, "thread/read"),
    _capability(
        "get_thread_snapshot",
        "threads",
        False,
        "thread/items/list",
        "thread/read",
        "thread/turns/list",
    ),
    _capability(
        "update_thread_settings",
        "execution",
        True,
        "thread/read",
        "thread/settings/update",
    ),
    _capability(
        "update_turn_settings", "execution", True, "thread/read", "turn/settings/update"
    ),
    _capability(
        "get_thread_goal", "execution", False, "thread/goal/get", "thread/read"
    ),
    _capability("set_thread_goal", "execution", True, "thread/goal/set", "thread/read"),
    _capability(
        "clear_thread_goal", "execution", True, "thread/goal/clear", "thread/read"
    ),
    _capability("list_models", "inspection", False, "model/list"),
    _capability(
        "read_model_provider_capabilities",
        "inspection",
        False,
        "modelProvider/capabilities/read",
    ),
    _capability("read_server_diagnostics", "inspection", False, "server/diagnostics"),
    _capability(
        "list_mcp_server_status", "integrations", False, "mcpServerStatus/list"
    ),
    _capability("list_loaded_threads", "threads", False, "thread/loaded/list"),
    _capability(
        "start_thread",
        "execution",
        True,
        "thread/read",
        "thread/settings/update",
        "thread/start",
    ),
    _capability(
        "create_thread_and_start_turn",
        "execution",
        True,
        "thread/read",
        "thread/resume",
        "thread/start",
        "turn/start",
    ),
    _capability(
        "resume_thread",
        "execution",
        True,
        "thread/read",
        "thread/resume",
        "thread/settings/update",
    ),
    _capability(
        "start_turn", "execution", True, "thread/read", "thread/resume", "turn/start"
    ),
    _capability("steer_turn", "execution", True, "thread/read", "turn/steer"),
    _capability("interrupt_turn", "execution", True, "thread/read", "turn/interrupt"),
)
CAPABILITIES = MappingProxyType(
    {capability.name: capability for capability in _CAPABILITY_RECORDS}
)
ACTION_NAMES_BY_PROFILE = MappingProxyType(
    {
        profile: frozenset(
            name
            for name, capability in CAPABILITIES.items()
            if profile in capability.profiles
        )
        for profile in ("operator", "observe")
    }
)
PACKAGE_ACTION_NAMES = MappingProxyType(
    {
        package: frozenset(
            name
            for name, capability in CAPABILITIES.items()
            if package in capability.packages
        )
        for package in (COMPATIBILITY_PACKAGE, OBSERVE_PACKAGE, CONTROL_PACKAGE)
    }
)


def action_names_for_profile(profile: str | None = None) -> frozenset[str]:
    if profile is None:
        profile = IMPLEMENTED_PROFILE
    try:
        return ACTION_NAMES_BY_PROFILE[profile]
    except KeyError as error:
        raise ValueError(f"Unsupported action exposure profile: {profile}") from error


def _validate_packages(packages: tuple[str, ...]) -> tuple[str, ...]:
    if not packages or any(not package for package in packages):
        raise ValueError("Action package selection must not be empty")
    unknown = set(packages) - PACKAGE_ACTION_NAMES.keys()
    if unknown:
        raise ValueError("Unsupported action package: " + ", ".join(sorted(unknown)))
    if len(set(packages)) != len(packages):
        raise ValueError("Duplicate action package selection")
    if COMPATIBILITY_PACKAGE in packages and len(packages) > 1:
        raise ValueError(
            "Compatibility action package cannot be combined with split packages"
        )
    return packages


def selected_package_names() -> tuple[str, ...]:
    selection = os.environ.get("CODEX_ACTION_PACKAGES", COMPATIBILITY_PACKAGE)
    return _validate_packages(
        tuple(package.strip() for package in selection.split(","))
    )


def action_names_for_deployment(
    profile: str | None = None, packages: tuple[str, ...] | None = None
) -> frozenset[str]:
    allowed = action_names_for_profile(profile)
    packages = (
        selected_package_names() if packages is None else _validate_packages(packages)
    )
    effective_profile = IMPLEMENTED_PROFILE if profile is None else profile
    if effective_profile == "observe" and CONTROL_PACKAGE in packages:
        raise ValueError("The observe profile cannot select the control package")
    names = frozenset(
        name for package in packages for name in PACKAGE_ACTION_NAMES[package]
    )
    return names & allowed


def native_methods_for_deployment(
    profile: str | None = None, packages: tuple[str, ...] | None = None
) -> frozenset[str]:
    return frozenset(
        method
        for name in action_names_for_deployment(profile, packages)
        for method in CAPABILITIES[name].native_methods
    )


EXPECTED_ACTION_NAMES = action_names_for_profile()
