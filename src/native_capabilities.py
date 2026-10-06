"""Pinned native capability inventory; this is descriptive, never an RPC allowlist."""

from codex_rpc import METHODS

SCHEMA_VERSION = "0.160.1"
SCHEMA_SOURCE = "openai/codex rust-v0.160.1 (d27764b82f7118f674371e6d6e76271d9d606edb)"
CONTRACT_VERSION = "0.1.0"

OBSERVE_METHODS = frozenset(
    {
        "mcpServerStatus/list",
        "model/list",
        "modelProvider/capabilities/read",
        "server/diagnostics",
        "thread/goal/get",
        "thread/items/list",
        "thread/list",
        "thread/loaded/list",
        "thread/read",
        "thread/turns/list",
    }
)
OPERATOR_CONTROL_METHODS = frozenset(METHODS) - OBSERVE_METHODS
CAS_ADDITIVE_METHODS = frozenset(
    {
        "thread/archive",
        "thread/compact/start",
        "thread/delete",
        "thread/fork",
        "thread/metadata/update",
        "thread/name/set",
        "thread/revert",
        "thread/unarchive",
    }
)

FAMILIES = (
    {
        "classification": "OBSERVE",
        "status": "supported",
        "methods": sorted(OBSERVE_METHODS),
        "reason": "Typed, target-scoped reads exposed in the operator profile.",
    },
    {
        "classification": "OPERATOR_CONTROL",
        "status": "supported",
        "methods": sorted(OPERATOR_CONTROL_METHODS),
        "reason": "Typed control methods exposed in the operator profile.",
    },
    {
        "classification": "EXPERIMENTAL",
        "status": "partially_supported",
        "methods": [
            "server/diagnostics",
            "thread/settings/update",
            "thread/queue/add",
            "thread/queue/list",
            "thread/queue/update",
            "thread/queue/delete",
            "thread/queue/reorder",
            "thread/queue/start",
            "thread/search",
            "thread/searchOccurrences",
            "thread/timeline/list",
            "turn/settings/update",
        ],
        "exposed_methods": [
            "server/diagnostics",
            "thread/settings/update",
            "turn/settings/update",
        ],
        "reason": "Experimental protocol methods require explicit CAS mappings; unexposed methods are never inferred from daemon support.",
    },
    {
        "classification": "ADMIN",
        "status": "not_exposed",
        "methods": [
            "account/gatewayOAuth/login",
            "account/bedrock/setup",
            "account/login/start",
            "account/logout",
            "command/exec",
            "command/exec/resize",
            "command/exec/terminate",
            "command/exec/write",
            "config/batchWrite",
            "config/value/write",
            "environment/add",
            "fs/copy",
            "fs/createDirectory",
            "fs/remove",
            "fs/unwatch",
            "fs/watch",
            "fs/writeFile",
            "experimentalFeature/enablement/set",
            "marketplace/add",
            "marketplace/remove",
            "marketplace/upgrade",
            "memory/reset",
            "mcpServer/oauth/login",
            "plugin/install",
            "plugin/uninstall",
            "process/spawn",
            "remoteControl/client/revoke",
            "remoteControl/pairing/start",
            "skills/config/write",
            "skills/extraRoots/set",
            "plugin/share/save",
            "plugin/share/updateTargets",
            "plugin/share/checkout",
            "plugin/share/delete",
        ],
        "reason": "Host, credential, package, process, and configuration mutations are absent from the ordinary operator profile; no caller-selectable elevation exists.",
    },
    {
        "classification": "CALLBACK",
        "status": "unsupported",
        "methods": [
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "item/permissions/requestApproval",
            "item/tool/requestUserInput",
            "mcpServer/elicitation/request",
            "item/tool/call",
        ],
        "reason": "The native callback is tied to the originating bidirectional WebSocket. CAS closes that connection at action completion and has no durable/resumable fenced-response protocol; it fails closed and never auto-approves.",
    },
    {
        "classification": "DEFERRED/UNSUPPORTED",
        "status": "not_exposed",
        "methods": [
            "account/rateLimits/read",
            "account/usage/read",
            "app/list",
            "hooks/list",
            "mcpServer/resource/read",
            "mcpServer/tool/call",
            "plugin/list",
            "plugin/read",
            "review/start",
            "skills/list",
            "thread/attachment/add",
            "thread/attachment/list",
            "thread/attachment/remove",
            "thread/inject_items",
            "thread/section/move",
            "thread/backgroundTerminals/list",
            "thread/backgroundTerminals/terminate",
        ],
        "reason": "Native methods are present in the pinned schema but do not yet have reviewed typed CAS request/response mappings and regression coverage.",
    },
)


def inventory(native_version: str | None) -> dict:
    return {
        "native_codex_version": native_version or "unavailable",
        "native_schema_version": SCHEMA_VERSION,
        "native_schema_source": SCHEMA_SOURCE,
        "schema_request_counts": {
            "default_client_requests": 104,
            "experimental_client_requests": 167,
            "experimental_only_client_requests": 63,
        },
        "cas_contract_version": CONTRACT_VERSION,
        "server_exposure_profile": "operator",
        "admin_enabled": False,
        "families": [
            {
                **family,
                "methods": list(family["methods"]),
                **(
                    {"exposed_methods": list(family["exposed_methods"])}
                    if "exposed_methods" in family
                    else {}
                ),
            }
            for family in FAMILIES
        ],
    }
