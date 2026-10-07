"""Typed compatibility entrypoints for the Codex Action Server package."""

from __future__ import annotations

from actions import Response
from typing import Any
from capability_registration import action
from codex_shared import execution
from codex_shared import inspection
from codex_shared import integrations
from codex_shared import organization
from codex_shared import queues
from codex_shared import threads
from codex_shared.models import AccountRateLimitsRequest as AccountRateLimitsRequest
from codex_shared.models import AccountUsageRequest as AccountUsageRequest
from codex_shared.models import AppReadRequest as AppReadRequest
from codex_shared.models import AppsListRequest as AppsListRequest
from codex_shared.models import ConnectionInfo as ConnectionInfo
from codex_shared.models import (
    CreateThreadAndStartTurnRequest as CreateThreadAndStartTurnRequest,
)
from codex_shared.models import CwdInventoryRequest as CwdInventoryRequest
from codex_shared.models import DispatchReceiptRequest as DispatchReceiptRequest
from codex_shared.models import EffectiveConfiguration as EffectiveConfiguration
from codex_shared.models import LoadedThreadListRequest as LoadedThreadListRequest
from codex_shared.models import McpResourceReadRequest as McpResourceReadRequest
from codex_shared.models import McpServerStatusListRequest as McpServerStatusListRequest
from codex_shared.models import McpToolCallRequest as McpToolCallRequest
from codex_shared.models import ModelListRequest as ModelListRequest
from codex_shared.models import (
    ModelProviderCapabilitiesRequest as ModelProviderCapabilitiesRequest,
)
from codex_shared.models import NativeCapabilitiesRequest as NativeCapabilitiesRequest
from codex_shared.models import PluginReadRequest as PluginReadRequest
from codex_shared.models import ReviewBaseBranchTarget as ReviewBaseBranchTarget
from codex_shared.models import ReviewCommitTarget as ReviewCommitTarget
from codex_shared.models import ReviewCustomTarget as ReviewCustomTarget
from codex_shared.models import ReviewStartRequest as ReviewStartRequest
from codex_shared.models import ReviewTarget as ReviewTarget
from codex_shared.models import (
    ReviewUncommittedChangesTarget as ReviewUncommittedChangesTarget,
)
from codex_shared.models import RpcEnvelope as RpcEnvelope
from codex_shared.models import ServerDiagnosticsRequest as ServerDiagnosticsRequest
from codex_shared.models import SortDirection as SortDirection
from codex_shared.models import StrictModel as StrictModel
from codex_shared.models import TargetName as TargetName
from codex_shared.models import ThreadAttachmentAddRequest as ThreadAttachmentAddRequest
from codex_shared.models import (
    ThreadAttachmentListRequest as ThreadAttachmentListRequest,
)
from codex_shared.models import (
    ThreadAttachmentRemoveRequest as ThreadAttachmentRemoveRequest,
)
from codex_shared.models import (
    ThreadBackgroundTerminalTerminateRequest as ThreadBackgroundTerminalTerminateRequest,
)
from codex_shared.models import (
    ThreadBackgroundTerminalsListRequest as ThreadBackgroundTerminalsListRequest,
)
from codex_shared.models import ThreadForkRequest as ThreadForkRequest
from codex_shared.models import ThreadGitInfoPatch as ThreadGitInfoPatch
from codex_shared.models import ThreadGoalClearRequest as ThreadGoalClearRequest
from codex_shared.models import ThreadGoalGetRequest as ThreadGoalGetRequest
from codex_shared.models import ThreadGoalSetRequest as ThreadGoalSetRequest
from codex_shared.models import ThreadGoalStatus as ThreadGoalStatus
from codex_shared.models import ThreadInjectItemsRequest as ThreadInjectItemsRequest
from codex_shared.models import ThreadInjectedTextContent as ThreadInjectedTextContent
from codex_shared.models import ThreadInjectedTextMessage as ThreadInjectedTextMessage
from codex_shared.models import ThreadItemsListRequest as ThreadItemsListRequest
from codex_shared.models import ThreadListRequest as ThreadListRequest
from codex_shared.models import (
    ThreadMetadataUpdateRequest as ThreadMetadataUpdateRequest,
)
from codex_shared.models import ThreadMutationRequest as ThreadMutationRequest
from codex_shared.models import ThreadNameSetRequest as ThreadNameSetRequest
from codex_shared.models import ThreadPageRequest as ThreadPageRequest
from codex_shared.models import ThreadQueueAddRequest as ThreadQueueAddRequest
from codex_shared.models import ThreadQueueDeleteRequest as ThreadQueueDeleteRequest
from codex_shared.models import ThreadQueueListRequest as ThreadQueueListRequest
from codex_shared.models import ThreadQueueReorderRequest as ThreadQueueReorderRequest
from codex_shared.models import ThreadQueueStartRequest as ThreadQueueStartRequest
from codex_shared.models import ThreadQueueUpdateRequest as ThreadQueueUpdateRequest
from codex_shared.models import ThreadReadRequest as ThreadReadRequest
from codex_shared.models import ThreadResumeRequest as ThreadResumeRequest
from codex_shared.models import ThreadRevertRequest as ThreadRevertRequest
from codex_shared.models import (
    ThreadSearchOccurrencesRequest as ThreadSearchOccurrencesRequest,
)
from codex_shared.models import ThreadSearchRequest as ThreadSearchRequest
from codex_shared.models import ThreadSearchSortKey as ThreadSearchSortKey
from codex_shared.models import ThreadSectionCreateRequest as ThreadSectionCreateRequest
from codex_shared.models import ThreadSectionDeleteRequest as ThreadSectionDeleteRequest
from codex_shared.models import ThreadSectionListRequest as ThreadSectionListRequest
from codex_shared.models import ThreadSectionMoveRequest as ThreadSectionMoveRequest
from codex_shared.models import ThreadSectionUpdateRequest as ThreadSectionUpdateRequest
from codex_shared.models import ThreadSettingsFields as ThreadSettingsFields
from codex_shared.models import (
    ThreadSettingsUpdateRequest as ThreadSettingsUpdateRequest,
)
from codex_shared.models import ThreadSnapshotRequest as ThreadSnapshotRequest
from codex_shared.models import ThreadSourceKind as ThreadSourceKind
from codex_shared.models import ThreadStartRequest as ThreadStartRequest
from codex_shared.models import ThreadTimelineListRequest as ThreadTimelineListRequest
from codex_shared.models import ThreadTurnsListRequest as ThreadTurnsListRequest
from codex_shared.models import TurnInterruptRequest as TurnInterruptRequest
from codex_shared.models import TurnItemsView as TurnItemsView
from codex_shared.models import TurnSettingsUpdateRequest as TurnSettingsUpdateRequest
from codex_shared.models import TurnStartRequest as TurnStartRequest
from codex_shared.models import TurnSteerRequest as TurnSteerRequest


@action(package="codex-action-server")
def read_dispatch_receipt(payload: DispatchReceiptRequest) -> Response[dict[str, Any]]:
    """Read a dispatch acknowledgement; native Codex owns current turn status.

    Args:
        payload: The exact client-generated request ID.
    """
    return inspection.read_dispatch_receipt(payload)


@action(package="codex-action-server")
def list_targets() -> Response[dict[str, Any]]:
    """List operator-configured logical target names without exposing credentials."""
    return inspection.list_targets()


@action(package="codex-action-server")
def inspect_target(payload: ServerDiagnosticsRequest) -> Response[dict[str, Any]]:
    """Resolve a logical target without starting services or changing native state.

    Args:
        payload: The configured logical target to resolve.
    """
    return inspection.inspect_target(payload)


@action(package="codex-action-server")
def list_native_capabilities(
    payload: NativeCapabilitiesRequest,
) -> Response[RpcEnvelope]:
    """List the pinned native inventory and this server's exposure profile.

    Args:
        payload: The configured target whose native version should be reported.
    """
    return inspection.list_native_capabilities(payload)


@action(package="codex-action-server")
def discover_threads(payload: ThreadListRequest) -> Response[RpcEnvelope]:
    """Discover persisted threads in one exact worktree on a configured target.

    Args:
        payload: Target, required exact absolute cwd, and bounded pagination controls.
    """
    return threads.discover_threads(payload)


@action(package="codex-action-server")
def fork_thread(payload: ThreadForkRequest) -> Response[RpcEnvelope]:
    """Fork one exact persisted thread without hydrating its transcript.

    Args:
        payload: Exact target, cwd, source thread, and bounded fork overrides.
    """
    return organization.fork_thread(payload)


@action(package="codex-action-server")
def archive_thread(payload: ThreadMutationRequest) -> Response[RpcEnvelope]:
    """Archive the exact persisted thread.

    Args:
        payload: Exact target, cwd, thread ID, and required dispatch receipt key.
    """
    return organization.archive_thread(payload)


@action(package="codex-action-server")
def unarchive_thread(payload: ThreadMutationRequest) -> Response[RpcEnvelope]:
    """Unarchive the exact persisted thread.

    Args:
        payload: Exact target, cwd, thread ID, and required dispatch receipt key.
    """
    return organization.unarchive_thread(payload)


@action(package="codex-action-server")
def delete_thread(payload: ThreadMutationRequest) -> Response[RpcEnvelope]:
    """Delete the exact persisted thread.

    Args:
        payload: Exact target, cwd, thread ID, and required dispatch receipt key.
    """
    return organization.delete_thread(payload)


@action(package="codex-action-server")
def set_thread_name(payload: ThreadNameSetRequest) -> Response[RpcEnvelope]:
    """Set the user-facing name of one exact persisted thread.

    Args:
        payload: Exact target, cwd, thread ID, name, and receipt key.
    """
    return organization.set_thread_name(payload)


@action(package="codex-action-server")
def update_thread_metadata(
    payload: ThreadMetadataUpdateRequest,
) -> Response[RpcEnvelope]:
    """Patch Git metadata without changing omitted fields.

    Args:
        payload: Exact thread identity and at least one typed metadata field.
    """
    return organization.update_thread_metadata(payload)


@action(package="codex-action-server")
def revert_thread(payload: ThreadRevertRequest) -> Response[RpcEnvelope]:
    """Revert persisted history to the prefix before one native turn ID.

    Args:
        payload: Exact thread identity and the native before-turn identifier.
    """
    return organization.revert_thread(payload)


@action(package="codex-action-server")
def compact_thread(payload: ThreadMutationRequest) -> Response[RpcEnvelope]:
    """Request native compaction for the exact persisted thread.

    Args:
        payload: Exact target, cwd, thread ID, and required dispatch receipt key.
    """
    return organization.compact_thread(payload)


@action(package="codex-action-server")
def start_review(payload: ReviewStartRequest) -> Response[RpcEnvelope]:
    """Start an inline native review on one exact thread.

    Args:
        payload: Exact thread identity, review target, and required receipt key.
    """
    return execution.start_review(payload)


@action(package="codex-action-server")
def read_account_rate_limits(
    payload: AccountRateLimitsRequest,
) -> Response[RpcEnvelope]:
    """Read native account rate-limit status without consuming credits.

    Args:
        payload: The configured target.
    """
    return inspection.read_account_rate_limits(payload)


@action(package="codex-action-server")
def read_account_usage(payload: AccountUsageRequest) -> Response[RpcEnvelope]:
    """Read account usage or a thread-scoped native usage estimate.

    Args:
        payload: Target and optional exact thread/CWD identity.
    """
    return inspection.read_account_usage(payload)


@action(package="codex-action-server")
def list_skills(payload: CwdInventoryRequest) -> Response[RpcEnvelope]:
    """List skills discovered for one exact worktree.

    Args:
        payload: Configured target and exact absolute worktree path.
    """
    return integrations.list_skills(payload)


@action(package="codex-action-server")
def list_hooks(payload: CwdInventoryRequest) -> Response[RpcEnvelope]:
    """List hooks discovered for one exact worktree.

    Args:
        payload: Configured target and exact absolute worktree path.
    """
    return integrations.list_hooks(payload)


@action(package="codex-action-server")
def list_plugins(payload: CwdInventoryRequest) -> Response[RpcEnvelope]:
    """List native plugins associated with one exact worktree.

    Args:
        payload: Configured target and exact absolute worktree path.
    """
    return integrations.list_plugins(payload)


@action(package="codex-action-server")
def read_plugin(payload: PluginReadRequest) -> Response[RpcEnvelope]:
    """Read one named plugin from the native plugin catalog.

    Args:
        payload: Configured target and exact native plugin name.
    """
    return integrations.read_plugin(payload)


@action(package="codex-action-server")
def list_apps(payload: AppsListRequest) -> Response[RpcEnvelope]:
    """List a bounded page of native apps/connectors.

    Args:
        payload: Target and bounded pagination; optional exact thread/CWD scope.
    """
    return integrations.list_apps(payload)


@action(package="codex-action-server")
def read_app(payload: AppReadRequest) -> Response[RpcEnvelope]:
    """Read bounded native metadata for one exact app identifier.

    Args:
        payload: Target, exact app ID, optional tool summaries, and optional thread/CWD.
    """
    return integrations.read_app(payload)


@action(package="codex-action-server")
def read_mcp_resource(payload: McpResourceReadRequest) -> Response[RpcEnvelope]:
    """Read one named native MCP resource with exact thread/CWD scope.

    Args:
        payload: Target, thread identity, server name, and resource URI.
    """
    return integrations.read_mcp_resource(payload)


@action(package="codex-action-server")
def call_mcp_tool(payload: McpToolCallRequest) -> Response[RpcEnvelope]:
    """Call one explicitly named native MCP tool under Codex's native authority.

    Args:
        payload: Exact thread, server/tool names, bounded arguments, and receipt key.
    """
    return integrations.call_mcp_tool(payload)


@action(package="codex-action-server")
def list_background_terminals(
    payload: ThreadBackgroundTerminalsListRequest,
) -> Response[RpcEnvelope]:
    """List a bounded page of terminals attached to one exact thread/worktree.

    Args:
        payload: Target, exact thread/CWD identity, and pagination controls.
    """
    return organization.list_background_terminals(payload)


@action(package="codex-action-server")
def terminate_background_terminal(
    payload: ThreadBackgroundTerminalTerminateRequest,
) -> Response[RpcEnvelope]:
    """Terminate a listed native terminal only after exact identity reconciliation.

    Args:
        payload: Exact thread/CWD, process ID, and required receipt key.
    """
    return organization.terminate_background_terminal(payload)


@action(package="codex-action-server")
def list_thread_attachments(
    payload: ThreadAttachmentListRequest,
) -> Response[RpcEnvelope]:
    """List a bounded attachment page from one exact thread.

    Args:
        payload: Exact target, cwd, thread ID, and pagination controls.
    """
    return organization.list_thread_attachments(payload)


@action(package="codex-action-server")
def add_thread_attachment(
    payload: ThreadAttachmentAddRequest,
) -> Response[RpcEnvelope]:
    """Create or locate one bounded, typed thread attachment.

    Args:
        payload: Exact thread identity, attachment type/key, bounded JSON payload,
            and required dispatch receipt key.
    """
    return organization.add_thread_attachment(payload)


@action(package="codex-action-server")
def remove_thread_attachment(
    payload: ThreadAttachmentRemoveRequest,
) -> Response[RpcEnvelope]:
    """Remove one thread attachment by its exact stable identity key.

    Args:
        payload: Exact thread, attachment type/key, and receipt key.
    """
    return organization.remove_thread_attachment(payload)


@action(package="codex-action-server")
def inject_thread_items(payload: ThreadInjectItemsRequest) -> Response[RpcEnvelope]:
    """Append a bounded list of typed user-text message items to one thread.

    Args:
        payload: Exact thread identity, restricted input-text items, and receipt key.
    """
    return organization.inject_thread_items(payload)


@action(package="codex-action-server")
def list_thread_sections(payload: ThreadSectionListRequest) -> Response[RpcEnvelope]:
    """List a bounded page of native thread sections for one configured target.

    Args:
        payload: Target and bounded pagination controls.
    """
    return threads.list_thread_sections(payload)


@action(package="codex-action-server")
def search_threads(payload: ThreadSearchRequest) -> Response[RpcEnvelope]:
    """Search a bounded page of threads on one target using the pinned experimental API.

    Args:
        payload: Target, non-empty search text, and bounded native search filters.
    """
    return threads.search_threads(payload)


@action(package="codex-action-server")
def search_thread_occurrences(
    payload: ThreadSearchOccurrencesRequest,
) -> Response[RpcEnvelope]:
    """Find bounded visible-message matches within one exact thread.

    Args:
        payload: Exact target, cwd, thread ID, query, and pagination controls.
    """
    return threads.search_thread_occurrences(payload)


@action(package="codex-action-server")
def list_thread_timeline(payload: ThreadTimelineListRequest) -> Response[RpcEnvelope]:
    """Read a bounded native timeline page for one exact thread.

    Args:
        payload: Exact target, cwd, thread ID, and pagination controls.
    """
    return threads.list_thread_timeline(payload)


@action(package="codex-action-server")
def list_thread_queue(payload: ThreadQueueListRequest) -> Response[RpcEnvelope]:
    """List a bounded native queue page for one exact thread.

    Args:
        payload: Exact target, cwd, thread ID, and page controls.
    """
    return queues.list_thread_queue(payload)


@action(package="codex-action-server")
def add_thread_queue_item(payload: ThreadQueueAddRequest) -> Response[RpcEnvelope]:
    """Add one bounded text submission to the exact native thread queue.

    Args:
        payload: Exact thread identity, client message ID, text, and receipt key.
    """
    return queues.add_thread_queue_item(payload)


@action(package="codex-action-server")
def update_thread_queue_item(
    payload: ThreadQueueUpdateRequest,
) -> Response[RpcEnvelope]:
    """Replace the typed text input for one exact queued submission.

    Args:
        payload: Exact thread and queue item identities, text, and receipt key.
    """
    return queues.update_thread_queue_item(payload)


@action(package="codex-action-server")
def delete_thread_queue_item(
    payload: ThreadQueueDeleteRequest,
) -> Response[RpcEnvelope]:
    """Delete one exact queued submission.

    Args:
        payload: Exact thread and queue item identities, plus receipt key.
    """
    return queues.delete_thread_queue_item(payload)


@action(package="codex-action-server")
def reorder_thread_queue(
    payload: ThreadQueueReorderRequest,
) -> Response[RpcEnvelope]:
    """Set the order of a bounded set of unique queued submission IDs.

    Args:
        payload: Exact thread identity and an ordered, unique ID list.
    """
    return queues.reorder_thread_queue(payload)


@action(package="codex-action-server")
def start_thread_queue(payload: ThreadQueueStartRequest) -> Response[RpcEnvelope]:
    """Start one exact queued submission or the native queue head.

    Args:
        payload: Exact thread identity and optional queued item ID.
    """
    return queues.start_thread_queue(payload)


@action(package="codex-action-server")
def create_thread_section(
    payload: ThreadSectionCreateRequest,
) -> Response[RpcEnvelope]:
    """Create one named native thread section.

    Args:
        payload: Configured target, bounded section name, and receipt key.
    """
    return organization.create_thread_section(payload)


@action(package="codex-action-server")
def update_thread_section(
    payload: ThreadSectionUpdateRequest,
) -> Response[RpcEnvelope]:
    """Rename one exact native thread section.

    Args:
        payload: Configured target, stable section ID, name, and receipt key.
    """
    return organization.update_thread_section(payload)


@action(package="codex-action-server")
def delete_thread_section(
    payload: ThreadSectionDeleteRequest,
) -> Response[RpcEnvelope]:
    """Delete one exact native thread section by its stable ID.

    Args:
        payload: Configured target, stable section ID, and receipt key.
    """
    return organization.delete_thread_section(payload)


@action(package="codex-action-server")
def move_thread_to_section(
    payload: ThreadSectionMoveRequest,
) -> Response[RpcEnvelope]:
    """Move one exact thread into a section or remove it from its section.

    Args:
        payload: Exact thread/CWD, destination section, optional insertion anchor, and receipt ID.
    """
    return organization.move_thread_to_section(payload)


@action(package="codex-action-server")
def list_thread_turns(payload: ThreadTurnsListRequest) -> Response[RpcEnvelope]:
    """Read one page of turns for an exact target-scoped workstream thread.

    Args:
        payload: Target, exact cwd/thread, and bounded turn-page options.
    """
    return threads.list_thread_turns(payload)


@action(package="codex-action-server")
def list_thread_items(payload: ThreadItemsListRequest) -> Response[RpcEnvelope]:
    """Read one page of items for an exact target-scoped workstream thread.

    Args:
        payload: Target, exact cwd/thread, optional turn, and item-page options.
    """
    return threads.list_thread_items(payload)


@action(package="codex-action-server")
def read_thread(payload: ThreadReadRequest) -> Response[RpcEnvelope]:
    """Read one target-scoped thread without changing it.

    Args:
        payload: Target, exact worktree cwd, and thread identifier.
    """
    return threads.read_thread(payload)


@action(package="codex-action-server")
def get_thread_snapshot(payload: ThreadSnapshotRequest) -> Response[RpcEnvelope]:
    """Return a byte-bounded native status projection for an exact thread/worktree.

    Args:
        payload: Target, exact CWD/thread identity, and optional prior revision.
    """
    return threads.get_thread_snapshot(payload)


@action(package="codex-action-server")
def update_thread_settings(
    payload: ThreadSettingsUpdateRequest,
) -> Response[RpcEnvelope]:
    """Update only model or reasoning effort for subsequent thread turns.

    Args:
        payload: Target, exact cwd/thread, and model or effort for later turns.
    """
    return execution.update_thread_settings(payload)


@action(package="codex-action-server")
def update_turn_settings(payload: TurnSettingsUpdateRequest) -> Response[RpcEnvelope]:
    """Apply native model or effort settings to one explicitly active turn.

    Args:
        payload: Target, exact cwd/thread/active-turn, and model or effort.
    """
    return execution.update_turn_settings(payload)


@action(package="codex-action-server")
def get_thread_goal(payload: ThreadGoalGetRequest) -> Response[RpcEnvelope]:
    """Read the native coordinator goal for one exact workstream thread.

    Args:
        payload: Target and exact worktree cwd/thread whose goal is read.
    """
    return execution.get_thread_goal(payload)


@action(package="codex-action-server")
def set_thread_goal(payload: ThreadGoalSetRequest) -> Response[RpcEnvelope]:
    """Set bounded native coordinator-goal fields for one exact thread.

    Args:
        payload: Target, exact cwd/thread, and objective, status, or token budget.
    """
    return execution.set_thread_goal(payload)


@action(package="codex-action-server")
def clear_thread_goal(payload: ThreadGoalClearRequest) -> Response[RpcEnvelope]:
    """Clear the native coordinator goal for one exact workstream thread.

    Args:
        payload: Target and exact worktree cwd/thread whose goal is cleared.
    """
    return execution.clear_thread_goal(payload)


@action(package="codex-action-server")
def list_models(payload: ModelListRequest) -> Response[RpcEnvelope]:
    """List the native model catalog without changing thread configuration.

    Args:
        payload: Target and optional bounded model-catalog page options.
    """
    return inspection.list_models(payload)


@action(package="codex-action-server")
def read_model_provider_capabilities(
    payload: ModelProviderCapabilitiesRequest,
) -> Response[RpcEnvelope]:
    """Read native provider capabilities for the selected target.

    Args:
        payload: Configured target whose native provider capabilities are read.
    """
    return inspection.read_model_provider_capabilities(payload)


@action(package="codex-action-server")
def read_server_diagnostics(payload: ServerDiagnosticsRequest) -> Response[RpcEnvelope]:
    """Read bounded native server process and gauge diagnostics.

    Args:
        payload: Configured target whose bounded native diagnostics are read.
    """
    return inspection.read_server_diagnostics(payload)


@action(package="codex-action-server")
def list_mcp_server_status(
    payload: McpServerStatusListRequest,
) -> Response[RpcEnvelope]:
    """List native MCP status with optional bounded inventory detail.

    Args:
        payload: Target, optional thread, bounded page, and inventory detail.
    """
    return integrations.list_mcp_server_status(payload)


@action(package="codex-action-server")
def list_loaded_threads(payload: LoadedThreadListRequest) -> Response[RpcEnvelope]:
    """List native thread ids currently loaded in memory.

    Args:
        payload: Target and optional bounded loaded-thread page options.
    """
    return threads.list_loaded_threads(payload)


@action(package="codex-action-server")
def start_thread(payload: ThreadStartRequest) -> Response[RpcEnvelope]:
    """Create a native thread rooted at the explicit absolute worktree path.

    Args:
        payload: Target and explicit absolute worktree cwd.
    """
    return execution.start_thread(payload)


@action(package="codex-action-server")
def create_thread_and_start_turn(
    payload: CreateThreadAndStartTurnRequest,
) -> Response[RpcEnvelope]:
    """Create a fresh thread and start its first typed turn on one connection.

    A new Codex 0.153.4 thread has no resumable rollout until its first turn;
    this action keeps creation and that first turn on the native connection.

    Args:
        payload: Target, exact cwd, first input, optional settings, and wait flag.
    """
    return execution.create_thread_and_start_turn(payload)


@action(package="codex-action-server")
def resume_thread(payload: ThreadResumeRequest) -> Response[RpcEnvelope]:
    """Attach the current native connection to an existing thread after cwd verification.

    Args:
        payload: Target, exact worktree cwd, and thread identifier.
    """
    return execution.resume_thread(payload)


@action(package="codex-action-server")
def start_turn(payload: TurnStartRequest) -> Response[RpcEnvelope]:
    """Resume, then start a text turn on the same native connection.

    Optional model and effort override native turn settings; the provider and
    execution policy are preserved. Omit them to retain the existing settings.

    Args:
        payload: Target, exact cwd, thread, text, and optional model/effort.
    """
    return execution.start_turn(payload)


@action(package="codex-action-server")
def steer_turn(payload: TurnSteerRequest) -> Response[RpcEnvelope]:
    """Steer only the explicitly named active turn after cwd verification.

    Args:
        payload: Target, exact worktree cwd, thread/turn identifiers, and steer text.
    """
    return execution.steer_turn(payload)


@action(package="codex-action-server")
def interrupt_turn(payload: TurnInterruptRequest) -> Response[RpcEnvelope]:
    """Interrupt only the explicitly named active turn after cwd verification.

    Args:
        payload: Target, exact worktree cwd, and thread/turn identifiers.
    """
    return execution.interrupt_turn(payload)
