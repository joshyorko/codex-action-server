"""Action Server entrypoints for a bounded Codex App Server RPC surface.

These are ordinary Action Server actions. Action Server owns the MCP endpoint;
this package does not start an MCP server or expose a shell command action.
"""

from __future__ import annotations

from functools import wraps
from datetime import datetime, timezone
import hashlib
from typing import Annotated, Any, Literal
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
from websockets.exceptions import ConnectionClosed
import dispatch_receipts

from pydantic import BaseModel, ConfigDict, Field, model_validator
from actions import ActionError, Response, action as _runtime_action

from action_catalog_contract import ACTION_NAMES_BY_PROFILE, action_names_for_profile
from boundary import configurations, resolve_target, validate_cwd, validate_identifier
from codex_rpc import Client, RpcError, native_server_build_version
from native_capabilities import inventory as native_capability_inventory

TargetName = str  # Runtime operator allowlist is authoritative, not baked-in hostnames.
SortDirection = Literal["asc", "desc"]
TurnItemsView = Literal["notLoaded", "summary", "full"]
ThreadSearchSortKey = Literal["created_at", "updated_at", "recency_at"]
ThreadSourceKind = Literal[
    "cli",
    "vscode",
    "exec",
    "appServer",
    "subAgent",
    "subAgentReview",
    "subAgentCompact",
    "subAgentThreadSpawn",
    "subAgentOther",
    "unknown",
]
ThreadGoalStatus = Literal[
    "active", "paused", "blocked", "usageLimited", "budgetLimited", "complete"
]

_ALL_ACTION_NAMES = ACTION_NAMES_BY_PROFILE["operator"]
_PROFILE_ACTION_NAMES = action_names_for_profile()


def action(*, is_consequential: bool):
    def decorate(function):
        name = function.__name__
        if name not in _ALL_ACTION_NAMES:
            raise ValueError(f"Action {name!r} is missing from the catalog contract")
        if name in _PROFILE_ACTION_NAMES:
            return _runtime_action(is_consequential=is_consequential)(function)

        @wraps(function)
        def denied(*_args, **_kwargs):
            raise ActionError(
                f"Action {name!r} is unavailable in the configured profile"
            )

        return denied

    return decorate


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ThreadListRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str = Field(
        description="Required exact absolute worktree path; discovery never spans worktrees"
    )
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = None


class ThreadReadRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str
    thread_id: str
    include_turns: bool = False


class ThreadSnapshotRequest(StrictModel):
    target: TargetName = Field(min_length=1, max_length=128)
    cwd: str = Field(min_length=1, max_length=512)
    thread_id: str = Field(min_length=1, max_length=512)
    revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ThreadSettingsFields(StrictModel):
    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=r"^\S+$",
        description="Optional native model override; omission preserves the current model.",
    )
    model_provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=r"^\S+$",
        description="Optional native model provider; omission preserves the current provider.",
    )
    effort: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^\S+$",
        description="Optional native reasoning effort; omission preserves the current effort.",
    )


class ThreadStartRequest(ThreadSettingsFields):
    request_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
        description="Persist one client-generated ID before dispatch. Reuse it only with identical arguments; retries return the receipt and never dispatch again.",
    )
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str


class CreateThreadAndStartTurnRequest(ThreadSettingsFields):
    request_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
        description="Persist one client-generated ID before dispatch. Reuse it only with identical arguments; retries return the receipt and never dispatch again.",
    )
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str
    text: str = Field(min_length=1)
    wait_for_completion: bool = False
    wait_seconds: float = Field(default=15, gt=0, le=30)
    enable_list_threads_callback: bool = Field(
        default=False,
        strict=True,
        description="Register only codex_app.list_threads on this connection; requires wait_for_completion.",
    )

    @model_validator(mode="after")
    def require_callback_wait(self):
        if self.enable_list_threads_callback and not self.wait_for_completion:
            raise ValueError(
                "enable_list_threads_callback requires wait_for_completion"
            )
        return self


class ThreadResumeRequest(ThreadSettingsFields):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str
    thread_id: str


class TurnStartRequest(StrictModel):
    request_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
        description="Persist one client-generated ID before dispatch. Reuse it only with identical arguments; retries return the receipt and never dispatch again.",
    )
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str
    thread_id: str
    text: str = Field(min_length=1)
    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=r"^\S+$",
        description="Optional native model override; omission preserves the thread model and provider.",
    )
    effort: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^\S+$",
        description="Optional native reasoning effort, for example max; omission preserves existing settings.",
    )
    wait_for_completion: bool = False
    wait_seconds: float = Field(default=15, gt=0, le=30)


class TurnSteerRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str
    thread_id: str
    turn_id: str
    text: str = Field(min_length=1)


class TurnInterruptRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str
    thread_id: str
    turn_id: str


class ThreadPageRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str
    thread_id: str
    limit: int | None = Field(default=None, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1)
    sort_direction: SortDirection | None = None


class ThreadTurnsListRequest(ThreadPageRequest):
    items_view: TurnItemsView | None = None


class ThreadItemsListRequest(ThreadPageRequest):
    turn_id: str | None = None


class ThreadSettingsUpdateRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str
    thread_id: str
    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=r"^\S+$",
    )
    effort: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^\S+$",
    )

    @model_validator(mode="after")
    def require_a_setting(self):
        if self.model is None and self.effort is None:
            raise ValueError("at least one of model or effort is required")
        return self


class TurnSettingsUpdateRequest(ThreadSettingsUpdateRequest):
    turn_id: str


class ThreadGoalGetRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    cwd: str
    thread_id: str


class ThreadGoalSetRequest(ThreadGoalGetRequest):
    objective: str | None = Field(default=None, min_length=1, max_length=4096)
    status: ThreadGoalStatus | None = None
    token_budget: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_a_goal_change(self):
        if self.objective is None and self.status is None and self.token_budget is None:
            raise ValueError("at least one goal field is required")
        return self


class ThreadGoalClearRequest(ThreadGoalGetRequest):
    pass


class ThreadMutationRequest(StrictModel):
    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    target: TargetName
    cwd: str
    thread_id: str


class ThreadForkRequest(ThreadMutationRequest):
    last_turn_id: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    model_provider: str | None = Field(default=None, min_length=1, max_length=256)


class ThreadNameSetRequest(ThreadMutationRequest):
    name: str = Field(min_length=1, max_length=256)


class ThreadGitInfoPatch(StrictModel):
    sha: str | None = Field(default=None, max_length=256)
    branch: str | None = Field(default=None, max_length=256)
    origin_url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def require_a_git_field(self):
        if not self.model_fields_set:
            raise ValueError("at least one git metadata field is required")
        return self


class ThreadMetadataUpdateRequest(ThreadMutationRequest):
    git_info: ThreadGitInfoPatch | None = None

    @model_validator(mode="after")
    def require_a_metadata_change(self):
        if self.git_info is None:
            raise ValueError("git_info is required")
        return self


class ThreadRevertRequest(ThreadMutationRequest):
    before_turn_id: str = Field(min_length=1, max_length=256)


class ThreadSectionMoveRequest(ThreadMutationRequest):
    section_id: str | None
    before_thread_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def require_destination_for_anchor(self):
        if self.section_id is None and self.before_thread_id is not None:
            raise ValueError("before_thread_id requires a destination section")
        return self


class ThreadSectionListRequest(StrictModel):
    target: TargetName
    limit: int | None = Field(default=None, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1)


class ThreadSearchRequest(StrictModel):
    target: TargetName
    cwd: str
    search_term: str = Field(min_length=1, max_length=512)
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1)
    sort_key: ThreadSearchSortKey | None = None
    sort_direction: SortDirection | None = None
    source_kinds: list[ThreadSourceKind] | None = Field(default=None, max_length=10)
    archived: bool | None = None


class ThreadSearchOccurrencesRequest(StrictModel):
    target: TargetName
    cwd: str
    thread_id: str
    search_term: str = Field(min_length=1, max_length=512)
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1)


class ThreadTimelineListRequest(StrictModel):
    target: TargetName
    cwd: str
    thread_id: str
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1)


class ThreadQueueListRequest(StrictModel):
    target: TargetName
    cwd: str
    thread_id: str
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1)


class ThreadQueueAddRequest(ThreadMutationRequest):
    text: str = Field(min_length=1, max_length=4096)
    client_user_message_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class ThreadQueueUpdateRequest(ThreadMutationRequest):
    queued_submission_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
    text: str = Field(min_length=1, max_length=4096)


class ThreadQueueDeleteRequest(ThreadMutationRequest):
    queued_submission_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")


class ThreadQueueReorderRequest(ThreadMutationRequest):
    queued_submission_ids: list[Annotated[str, Field(min_length=1, max_length=256)]] = (
        Field(min_length=1, max_length=100)
    )

    @model_validator(mode="after")
    def require_unique_submission_ids(self):
        if len(set(self.queued_submission_ids)) != len(self.queued_submission_ids):
            raise ValueError("queued_submission_ids must be unique")
        return self


class ThreadQueueStartRequest(ThreadMutationRequest):
    queued_submission_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$"
    )


class ReviewUncommittedChangesTarget(StrictModel):
    type: Literal["uncommittedChanges"]


class ReviewBaseBranchTarget(StrictModel):
    type: Literal["baseBranch"]
    branch: str = Field(min_length=1, max_length=256)


class ReviewCommitTarget(StrictModel):
    type: Literal["commit"]
    sha: str = Field(min_length=1, max_length=256)
    title: str | None = Field(default=None, max_length=512)


class ReviewCustomTarget(StrictModel):
    type: Literal["custom"]
    instructions: str = Field(min_length=1, max_length=4096)


ReviewTarget = Annotated[
    ReviewUncommittedChangesTarget
    | ReviewBaseBranchTarget
    | ReviewCommitTarget
    | ReviewCustomTarget,
    Field(discriminator="type"),
]


class ReviewStartRequest(ThreadMutationRequest):
    review_target: ReviewTarget


class AccountRateLimitsRequest(StrictModel):
    target: TargetName


class AccountUsageRequest(StrictModel):
    target: TargetName
    thread_id: str | None = None
    cwd: str | None = None

    @model_validator(mode="after")
    def require_exact_thread_scope(self):
        if (self.thread_id is None) != (self.cwd is None):
            raise ValueError("thread_id and cwd must be supplied together")
        return self


class CwdInventoryRequest(StrictModel):
    target: TargetName
    cwd: str


class PluginReadRequest(StrictModel):
    target: TargetName
    plugin_name: str = Field(min_length=1, max_length=256)


class AppsListRequest(StrictModel):
    target: TargetName
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1)
    thread_id: str | None = None
    cwd: str | None = None

    @model_validator(mode="after")
    def require_exact_thread_scope(self):
        if (self.thread_id is None) != (self.cwd is None):
            raise ValueError("thread_id and cwd must be supplied together")
        return self


class AppReadRequest(StrictModel):
    target: TargetName
    app_id: str = Field(min_length=1, max_length=256)
    include_tools: bool = False
    thread_id: str | None = None
    cwd: str | None = None

    @model_validator(mode="after")
    def require_exact_thread_scope(self):
        if (self.thread_id is None) != (self.cwd is None):
            raise ValueError("thread_id and cwd must be supplied together")
        return self


class McpResourceReadRequest(StrictModel):
    target: TargetName
    cwd: str
    thread_id: str
    server: str = Field(min_length=1, max_length=256)
    uri: str = Field(min_length=1, max_length=2048)


class McpToolCallRequest(ThreadMutationRequest):
    server: str = Field(min_length=1, max_length=256)
    tool: str = Field(min_length=1, max_length=256)
    arguments: dict[str, Any] | None = None

    @model_validator(mode="after")
    def bound_arguments(self):
        try:
            encoded = json.dumps(self.arguments, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as error:
            raise ValueError("arguments must be JSON-compatible") from error
        if len(encoded.encode("utf-8")) > 16_384:
            raise ValueError("arguments exceed the 16 KiB limit")
        return self


class ThreadBackgroundTerminalsListRequest(StrictModel):
    target: TargetName
    cwd: str
    thread_id: str
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1)


class ThreadBackgroundTerminalTerminateRequest(ThreadMutationRequest):
    process_id: str = Field(min_length=1, max_length=256)


class ThreadAttachmentListRequest(StrictModel):
    target: TargetName
    cwd: str
    thread_id: str
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1)


class ThreadAttachmentAddRequest(ThreadMutationRequest):
    attachment_type: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
    )
    identity_key: str = Field(min_length=1, max_length=256)
    payload: dict[str, Any]

    @model_validator(mode="after")
    def bound_payload(self):
        try:
            encoded = json.dumps(self.payload, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as error:
            raise ValueError("payload must be JSON-compatible") from error
        if len(encoded.encode("utf-8")) > 16_384:
            raise ValueError("payload exceeds the 16 KiB limit")
        return self


class ThreadAttachmentRemoveRequest(ThreadMutationRequest):
    attachment_type: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
    )
    identity_key: str = Field(min_length=1, max_length=256)


class ThreadInjectedTextContent(StrictModel):
    type: Literal["input_text"]
    text: str = Field(min_length=1, max_length=4096)


class ThreadInjectedTextMessage(StrictModel):
    type: Literal["message"]
    role: Literal["user"]
    content: list[ThreadInjectedTextContent] = Field(min_length=1, max_length=4)


class ThreadInjectItemsRequest(ThreadMutationRequest):
    items: list[ThreadInjectedTextMessage] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def bound_injected_text(self):
        total = sum(
            len(content.text.encode("utf-8"))
            for item in self.items
            for content in item.content
        )
        if total > 16_384:
            raise ValueError("injected text exceeds the 16 KiB limit")
        return self


class ThreadSectionCreateRequest(StrictModel):
    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    target: TargetName
    name: str = Field(min_length=1, max_length=128)


class ThreadSectionUpdateRequest(ThreadSectionCreateRequest):
    section_id: str = Field(min_length=1, max_length=256)


class ThreadSectionDeleteRequest(StrictModel):
    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    target: TargetName
    section_id: str = Field(min_length=1, max_length=256)


class ModelListRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    limit: int | None = Field(default=None, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1)
    include_hidden: bool | None = None


class ModelProviderCapabilitiesRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )


class ServerDiagnosticsRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )


class NativeCapabilitiesRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name; reports this daemon's advertised version"
    )


class McpServerStatusListRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    thread_id: str | None = None
    limit: int | None = Field(default=None, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1)
    detail: Literal["full", "toolsAndAuthOnly"] | None = None


class LoadedThreadListRequest(StrictModel):
    target: TargetName = Field(
        description="Configured target name, never a shell command"
    )
    limit: int | None = Field(default=None, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1)


class EffectiveConfiguration(StrictModel):
    model: str | None = None
    model_provider: str | None = None
    effort: str | None = None


class ConnectionInfo(StrictModel):
    target: str
    codex_bin: str
    socket: str | None = None
    codex_home: str | None = None
    server: str | None = None


class RpcEnvelope(StrictModel):
    operation: str
    connection: ConnectionInfo
    result: dict[str, Any]
    effective_configuration: EffectiveConfiguration | None = None
    receipts: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)


def _action_cwd(cwd: str) -> str:
    try:
        return validate_cwd(cwd)
    except ValueError as error:
        raise ActionError(str(error)) from error


def _action_id(value: str, field_name: str) -> str:
    try:
        return validate_identifier(value, field_name)
    except ValueError as error:
        raise ActionError(str(error)) from error


def _read_guarded_thread(
    client: Client, thread_id: str, cwd: str, *, include_turns: bool = False
) -> dict[str, Any]:
    actual = client.request(
        "thread/read", {"threadId": thread_id, "includeTurns": include_turns}
    )
    thread = actual.get("thread") if isinstance(actual, dict) else None
    if (
        not isinstance(thread, dict)
        or thread.get("id") != thread_id
        or thread.get("cwd") != cwd
    ):
        raise ActionError(
            "Stored thread id/cwd does not match the requested workstream"
        )
    return actual


def _guard_cwd(client: Client, thread_id: str, cwd: str) -> None:
    _read_guarded_thread(client, thread_id, cwd)


def _optional_thread_settings(payload) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if payload.model is not None:
        params["model"] = payload.model
    if payload.model_provider is not None:
        params["modelProvider"] = payload.model_provider
    return params


def _optional_page_params(payload) -> dict[str, Any]:
    params: dict[str, Any] = {"threadId": payload.thread_id}
    if payload.limit is not None:
        params["limit"] = payload.limit
    if payload.cursor is not None:
        params["cursor"] = payload.cursor
    if payload.sort_direction is not None:
        params["sortDirection"] = payload.sort_direction
    return params


def _bounded_native_result(result: dict[str, Any], maximum_bytes: int = 1_048_576):
    try:
        encoded = json.dumps(result, ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise RpcError("Native result is not bounded JSON data") from error
    if len(encoded) > maximum_bytes:
        raise RpcError("Native result exceeds the 1 MiB action limit")
    return result


SNAPSHOT_MAX_BYTES = 8192
SNAPSHOT_MESSAGE_MAX_BYTES = 512
_THREAD_STATUS_TYPES = {"active", "idle", "notLoaded", "systemError"}
_TURN_STATUS_TYPES = {"completed", "failed", "inProgress", "interrupted"}
_ACTIVE_FLAGS = {"waitingOnApproval", "waitingOnUserInput"}
_ERROR_CODES = {
    "activeTurnNotSteerable",
    "badRequest",
    "contextWindowExceeded",
    "cyberPolicy",
    "flexUnavailable",
    "internalServerError",
    "misalignmentPolicyViolation",
    "other",
    "rateLimitExceeded",
    "sandboxError",
    "serverOverloaded",
    "sessionBudgetExceeded",
    "threadRollbackFailed",
    "tooManyDenials",
    "unauthorized",
    "usageLimitExceeded",
}


def _utf8_truncate(value: str, maximum_bytes: int) -> tuple[str, bool]:
    if maximum_bytes < len("…".encode("utf-8")):
        return "", bool(value)
    size = 0
    result = []
    for character in value:
        width = len(character.encode("utf-8"))
        if size + width > maximum_bytes:
            while result and size + len("…".encode("utf-8")) > maximum_bytes:
                size -= len(result.pop().encode("utf-8"))
            if size + len("…".encode("utf-8")) > maximum_bytes:
                return "", True
            return "".join(result) + "…", True
        result.append(character)
        size += width
    return "".join(result), False


def _snapshot_error_code(error: Any) -> str | None:
    if not isinstance(error, dict):
        return None
    code = error.get("codexErrorInfo")
    if isinstance(code, dict) and len(code) == 1:
        code = next(iter(code))
    if isinstance(code, str) and code in _ERROR_CODES:
        return code
    return "unclassified" if isinstance(error.get("message"), str) else None


def _snapshot_unknown_state_digest(value: Any) -> str:
    digest = hashlib.sha256(b"codex-action-server.snapshot-unknown-state.v1\0")
    encoder = json.JSONEncoder(
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    for chunk in encoder.iterencode(value):
        digest.update(chunk.encode("ascii"))
    return digest.hexdigest()


def _user_message_excerpt(content: list[Any]) -> tuple[str, bool]:
    excerpts = []
    remaining = SNAPSHOT_MESSAGE_MAX_BYTES
    truncated = False
    for part in content:
        if (
            not isinstance(part, dict)
            or part.get("type") != "text"
            or not isinstance(part.get("text"), str)
        ):
            continue
        prefix = "\n" if excerpts else ""
        remaining -= len(prefix.encode("utf-8"))
        if remaining <= 0:
            truncated = True
            break
        text, text_truncated = _utf8_truncate(part["text"], remaining)
        excerpts.append(prefix + text)
        remaining -= len(text.encode("utf-8"))
        if text_truncated:
            truncated = True
            break
    return "".join(excerpts), truncated


def _snapshot_item(item_entry: Any, turn_id: str) -> dict[str, Any] | None:
    if item_entry is None:
        return None
    if (
        not isinstance(item_entry, dict)
        or item_entry.get("turnId") != turn_id
        or not isinstance(item_entry.get("item"), dict)
    ):
        raise RpcError("Native snapshot item identity mismatch")
    item = item_entry["item"]
    kind = item.get("type")
    if not isinstance(kind, str) or len(kind) > 64:
        raise RpcError("Native snapshot item type is invalid")
    item_id = item.get("id")
    if not isinstance(item_id, str) or not item_id or len(item_id) > 512:
        raise RpcError("Native snapshot item identity is invalid")
    status = item.get("status")
    projected_status = status if isinstance(status, str) and len(status) <= 64 else None
    phase = item.get("phase")
    projected_phase = phase if isinstance(phase, str) and len(phase) <= 64 else None
    text = None
    truncated = False
    if kind == "agentMessage" and isinstance(item.get("text"), str):
        text = item["text"]
    elif kind == "userMessage" and isinstance(item.get("content"), list):
        text, truncated = _user_message_excerpt(item["content"])
    if text is not None and kind == "agentMessage":
        text, truncated = _utf8_truncate(text, SNAPSHOT_MESSAGE_MAX_BYTES)
    return {
        "id": item_id,
        "kind": kind,
        "phase": projected_phase,
        "status": projected_status,
        "text": text,
        "text_truncated": truncated,
    }


def _project_thread_snapshot(
    target: str,
    cwd: str,
    thread_id: str,
    thread: dict[str, Any],
    turn: dict[str, Any] | None,
    latest_item: Any,
    item_cursor: Any,
) -> dict[str, Any]:
    native_status = thread.get("status")
    status_type = native_status.get("type") if isinstance(native_status, dict) else None
    known_status = (
        status_type
        if isinstance(status_type, str) and status_type in _THREAD_STATUS_TYPES
        else "unknown"
    )
    flags = (
        native_status.get("activeFlags", []) if isinstance(native_status, dict) else []
    )
    active_flags_missing = known_status == "active" and (
        not isinstance(native_status, dict) or "activeFlags" not in native_status
    )
    active_flags_invalid = (
        isinstance(native_status, dict)
        and "activeFlags" in native_status
        and not isinstance(flags, list)
    )
    active_flags = (
        sorted(
            flag for flag in flags if isinstance(flag, str) and flag in _ACTIVE_FLAGS
        )
        if isinstance(flags, list)
        else []
    )
    unknown_flags = (
        [
            flag
            for flag in flags
            if not isinstance(flag, str) or flag not in _ACTIVE_FLAGS
        ]
        if isinstance(flags, list)
        else flags
    )
    unknown_active_flags = (
        active_flags_missing
        or active_flags_invalid
        or (
            isinstance(flags, list)
            and any(
                not isinstance(flag, str) or flag not in _ACTIVE_FLAGS for flag in flags
            )
        )
    )
    turn_id = turn.get("id") if isinstance(turn, dict) else None
    turn_status = turn.get("status") if isinstance(turn, dict) else None
    turn_status_unknown = isinstance(turn_id, str) and (
        not isinstance(turn_status, str) or turn_status not in _TURN_STATUS_TYPES
    )
    projected_turn = (
        {
            **{
                "id": turn_id,
                "status": "unknown" if turn_status_unknown else turn_status,
                "error_code": _snapshot_error_code(turn.get("error")),
            },
            **({"native_status_unknown": True} if turn_status_unknown else {}),
        }
        if isinstance(turn, dict)
        else None
    )
    unknown_native_state = {}
    if known_status == "unknown":
        unknown_native_state["thread_status"] = {
            "present": "status" in thread,
            "type_present": isinstance(native_status, dict) and "type" in native_status,
            "value": status_type if isinstance(native_status, dict) else native_status,
        }
    if unknown_active_flags:
        unknown_native_state["active_flags"] = {
            "present": isinstance(native_status, dict)
            and "activeFlags" in native_status,
            "value": unknown_flags,
        }
    if turn_status_unknown:
        unknown_native_state["turn_status"] = {
            "present": isinstance(turn, dict) and "status" in turn,
            "value": turn_status,
        }
    cursor = (
        item_cursor
        if isinstance(item_cursor, str) and len(item_cursor) <= 512
        else None
    )
    cursor_digest = None
    if isinstance(item_cursor, str) and cursor is None:
        digest = hashlib.sha256()
        for offset in range(0, len(item_cursor), 1024):
            digest.update(item_cursor[offset : offset + 1024].encode("utf-8"))
        cursor_digest = digest.hexdigest()
    updated_at = thread.get("updatedAt")
    return {
        "target": target,
        "cwd": cwd,
        "thread_id": thread_id,
        "source": "native-app-server",
        "thread_status": known_status,
        "active_flags": active_flags,
        "unknown_active_flags": unknown_active_flags,
        "native_updated_at": updated_at
        if isinstance(updated_at, int) and not isinstance(updated_at, bool)
        else None,
        "native_updated_at_unknown": not (
            isinstance(updated_at, int) and not isinstance(updated_at, bool)
        ),
        "latest_turn": projected_turn,
        "latest_item": _snapshot_item(latest_item, turn_id)
        if isinstance(turn_id, str)
        else None,
        "_unknown_state_digest": _snapshot_unknown_state_digest(unknown_native_state)
        if unknown_native_state
        else None,
        "continuation": {
            "turn_id": turn_id if isinstance(turn_id, str) else None,
            "item_cursor": cursor,
            "item_cursor_omitted": item_cursor is not None and cursor is None,
            "item_cursor_digest": cursor_digest,
        },
    }


def _snapshot_response(projected: dict[str, Any], observed_at: str) -> dict[str, Any]:
    unknown_state_digest = projected.pop("_unknown_state_digest", None)
    revision = hashlib.sha256(
        json.dumps(
            {
                "projection": projected,
                "unknown_state_digest": unknown_state_digest,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    result = {
        **projected,
        "observed_at": observed_at,
        "revision": revision,
        "changed": True,
        "native_state_authoritative": True,
    }
    if (
        len(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        > SNAPSHOT_MAX_BYTES
    ):
        raise RpcError("Native supervisory snapshot exceeds its byte limit")
    return result


def _validate_app_read_response(result: Any, requested_app_id: str) -> None:
    if not isinstance(result, dict):
        raise RpcError("Native app/read response is not an object")
    apps = result.get("apps")
    missing = result.get("missingAppIds")
    if not isinstance(apps, list) or not isinstance(missing, list):
        raise RpcError("Native app/read response is missing its apps or missingAppIds")
    returned_ids = [app.get("id") if isinstance(app, dict) else None for app in apps]
    if (
        len(apps) > 1
        or returned_ids not in ([requested_app_id], [])
        or missing not in ([requested_app_id], [])
        or bool(apps) == bool(missing)
    ):
        raise RpcError("Native app/read response identity mismatch")
    if apps and not isinstance(apps[0].get("name"), str):
        raise RpcError("Native app/read metadata is missing its name")


def _effective_configuration(result: dict[str, Any]) -> EffectiveConfiguration | None:
    if not isinstance(result, dict):
        return None
    thread = result.get("thread")
    sources = [result]
    if isinstance(thread, dict):
        sources.append(thread)
    native = result.get("native")
    if isinstance(native, dict):
        # The effort-only update cannot change model/provider. Retain those
        # observations, but never promote pre-update effort to current effort.
        sources.append(
            {key: native[key] for key in ("model", "modelProvider") if key in native}
        )
    values = {
        "model": next(
            (source["model"] for source in sources if "model" in source), None
        ),
        "model_provider": next(
            (
                source["modelProvider"]
                for source in sources
                if "modelProvider" in source
            ),
            None,
        ),
        "effort": next(
            (
                source["reasoningEffort"]
                for source in sources
                if "reasoningEffort" in source
            ),
            None,
        ),
    }
    return (
        EffectiveConfiguration(**values)
        if any(value is not None for value in values.values())
        else None
    )


def _envelope(
    operation: str,
    client: Client,
    result: dict[str, Any],
    *,
    include_receipts: bool = True,
    include_events: bool = True,
) -> RpcEnvelope:
    provenance = client.provenance()
    return RpcEnvelope(
        operation=operation,
        connection=ConnectionInfo(
            target=provenance.get("target", ""),
            codex_bin=provenance.get("codexBin", ""),
            socket=provenance.get("socket"),
            codex_home=provenance.get("codexHome"),
            server=provenance.get("server"),
        ),
        result=result,
        effective_configuration=_effective_configuration(result),
        receipts=client.receipts if include_receipts else [],
        events=client.events if include_events else [],
    )


def _run(
    operation: str,
    target_name: str,
    callback,
    *,
    include_receipts: bool = True,
    include_events: bool = True,
    maximum_response_bytes: int | None = None,
) -> Response[RpcEnvelope]:
    try:
        target = resolve_target(target_name)
        with Client(target) as client:
            result = callback(client)
            response = Response(
                result=_envelope(
                    operation,
                    client,
                    result,
                    include_receipts=include_receipts,
                    include_events=include_events,
                )
            )
            if maximum_response_bytes is not None:
                encoded = json.dumps(
                    response.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                if len(encoded) > maximum_response_bytes:
                    raise RpcError("Native supervisory snapshot exceeds its byte limit")
            return response
    except ActionError:
        raise
    except (
        RpcError,
        OSError,
        TimeoutError,
        ValueError,
        KeyError,
        subprocess.SubprocessError,
        ConnectionClosed,
    ) as error:
        raise ActionError(
            f"{operation} failed closed ({type(error).__name__}); inspect target diagnostics"
        ) from None


class _UnkeyedReceipt:
    replayed = False

    def __init__(self):
        self.data = {"state": "unknown", "request_id": None}

    def update(self, **values):
        self.data.update(values)

    def public(self):
        return {
            **self.data,
            "replayed": False,
            "retry_same_request_id": "unsafe_without_request_id",
            "native_state_authoritative": True,
        }


def _receipt_root():
    configured = os.environ.get("CODEX_ACTION_RECEIPTS")
    return (
        Path(configured)
        if configured
        else Path.home() / ".local/state/codex-action-server/receipts"
    )


def _dispatch_run(operation, payload, callback):
    # Reserve before connecting. A transport failure cannot trigger implicit replay.
    scope = (
        dispatch_receipts.reserve(
            _receipt_root(),
            payload.request_id,
            {"operation": operation, **payload.model_dump()},
        )
        if payload.request_id
        else nullcontext(_UnkeyedReceipt())
    )
    try:
        with scope as receipt:
            if receipt.replayed:
                return Response(
                    result=RpcEnvelope(
                        operation=operation,
                        connection=ConnectionInfo(target=payload.target, codex_bin=""),
                        result={"dispatch": receipt.public()},
                    )
                )
            client = None
            try:
                target = resolve_target(payload.target)
                with Client(target) as client:
                    receipt.update(
                        wait_mode="bounded"
                        if getattr(payload, "wait_for_completion", False)
                        else "dispatch"
                    )
                    result = callback(client, receipt)
                    result["dispatch"] = receipt.public()
                    return Response(result=_envelope(operation, client, result))
            except (
                ActionError,
                RpcError,
                OSError,
                TimeoutError,
                ValueError,
                KeyError,
                subprocess.SubprocessError,
                ConnectionClosed,
            ) as error:
                receipt.update(error_code=type(error).__name__)
                envelope = RpcEnvelope(
                    operation=operation,
                    connection=ConnectionInfo(target=payload.target, codex_bin=""),
                    result={"dispatch": receipt.public()},
                )
                return Response(result=envelope)
    except (ValueError, OSError) as error:
        raise ActionError(
            f"dispatch receipt rejected ({type(error).__name__})"
        ) from None


class DispatchReceiptRequest(StrictModel):
    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@action(is_consequential=False)
def read_dispatch_receipt(payload: DispatchReceiptRequest) -> Response[dict[str, Any]]:
    """Read a dispatch acknowledgement; native Codex owns current turn status.

    Args:
        payload: The exact client-generated request ID.
    """
    return Response(result=dispatch_receipts.read(_receipt_root(), payload.request_id))


@action(is_consequential=False)
def list_targets() -> Response[dict[str, Any]]:
    """List operator-configured logical target names without exposing credentials."""
    return Response(
        result={
            "targets": [
                {"name": name, "transport": config.get("transport")}
                for name, config in configurations().items()
            ]
        }
    )


@action(is_consequential=False)
def inspect_target(payload: ServerDiagnosticsRequest) -> Response[dict[str, Any]]:
    """Resolve a logical target without starting services or changing native state.

    Args:
        payload: The configured logical target to resolve.
    """
    try:
        target = resolve_target(payload.target)
        return Response(
            result={
                "target": payload.target,
                "status": "resolved",
                "destination": target.target,
                "codex_bin": target.codex_bin,
                "socket": target.socket_path,
                "connectivity": "not_probed",
            }
        )
    except ValueError as error:
        return Response(
            result={
                "target": payload.target,
                "status": "unresolved",
                "error_code": str(error),
            }
        )


@action(is_consequential=False)
def list_native_capabilities(
    payload: NativeCapabilitiesRequest,
) -> Response[RpcEnvelope]:
    """List the pinned native inventory and this server's exposure profile.

    Args:
        payload: The configured target whose native version should be reported.
    """
    return _run(
        "list_native_capabilities",
        payload.target,
        lambda client: native_capability_inventory(
            native_server_build_version(client.metadata.get("userAgent"))
        ),
    )


def _apply_thread_effort(
    client: Client,
    native_result: dict[str, Any],
    thread_id: str,
    cwd: str,
    effort: str | None,
) -> dict[str, Any]:
    if effort is None:
        return native_result
    settings = client.request(
        "thread/settings/update", {"threadId": thread_id, "effort": effort}
    )
    current = _read_guarded_thread(client, thread_id, cwd)
    return {
        "native": native_result,
        "settings": settings,
        "thread": current["thread"],
    }


def _thread_control(
    operation: str,
    payload: ThreadMutationRequest,
    method: str,
    params: dict[str, Any],
    *,
    forked: bool = False,
) -> Response[RpcEnvelope]:
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)

    def invoke(client, receipt):
        _read_guarded_thread(client, thread_id, cwd)
        result = client.request(method, {"threadId": thread_id, **params})
        if forked:
            thread = result.get("thread") if isinstance(result, dict) else None
            if (
                not isinstance(thread, dict)
                or not isinstance(thread.get("id"), str)
                or not thread["id"]
                or thread.get("cwd") != cwd
            ):
                raise ActionError("Native fork response thread/cwd identity mismatch")
            receipt.update(state="accepted", thread_id=thread["id"])
        else:
            receipt.update(state="accepted", thread_id=thread_id)
        return result

    return _dispatch_run(operation, payload, invoke)


def _thread_queue_control(
    operation: str,
    payload: ThreadMutationRequest,
    method: str,
    params: dict[str, Any],
    *,
    expected_submission_id: str | None = None,
    starts_turn: bool = False,
) -> Response[RpcEnvelope]:
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)

    def invoke(client, receipt):
        _read_guarded_thread(client, thread_id, cwd)
        result = client.request(method, {"threadId": thread_id, **params})
        if expected_submission_id is not None:
            queued = (
                result.get("queuedSubmission") if isinstance(result, dict) else None
            )
            if (
                not isinstance(queued, dict)
                or queued.get("id") != expected_submission_id
            ):
                raise RpcError("Native queued submission identity mismatch")
        elif method == "thread/queue/add":
            queued = (
                result.get("queuedSubmission") if isinstance(result, dict) else None
            )
            if not isinstance(queued, dict) or not isinstance(queued.get("id"), str):
                raise RpcError("Native queued submission response is missing its ID")
        if method == "thread/queue/delete" and (
            not isinstance(result, dict) or result.get("deleted") is not True
        ):
            raise RpcError("Native queue delete did not confirm deletion")
        if starts_turn:
            turn = result.get("turn") if isinstance(result, dict) else None
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            if not isinstance(turn_id, str) or not turn_id:
                raise RpcError("Native queued turn response is missing its turn ID")
            receipt.update(state="accepted", thread_id=thread_id, turn_id=turn_id)
        else:
            receipt.update(state="accepted", thread_id=thread_id)
        return result

    return _dispatch_run(operation, payload, invoke)


@action(is_consequential=False)
def discover_threads(payload: ThreadListRequest) -> Response[RpcEnvelope]:
    """Discover persisted threads in one exact worktree on a configured target.

    Args:
        payload: Target, required exact absolute cwd, and bounded pagination controls.
    """
    params: dict[str, Any] = {
        "cwd": _action_cwd(payload.cwd),
        "limit": payload.limit,
    }
    if payload.cursor is not None:
        params["cursor"] = payload.cursor
    return _run(
        "thread/list",
        payload.target,
        lambda client: client.request("thread/list", params),
    )


@action(is_consequential=True)
def fork_thread(payload: ThreadForkRequest) -> Response[RpcEnvelope]:
    """Fork one exact persisted thread without hydrating its transcript.

    Args:
        payload: Exact target, cwd, source thread, and bounded fork overrides.
    """
    params: dict[str, Any] = {"cwd": _action_cwd(payload.cwd), "excludeTurns": True}
    for key, value in (
        ("lastTurnId", payload.last_turn_id),
        ("model", payload.model),
        ("modelProvider", payload.model_provider),
    ):
        if value is not None:
            params[key] = value
    return _thread_control("fork_thread", payload, "thread/fork", params, forked=True)


@action(is_consequential=True)
def archive_thread(payload: ThreadMutationRequest) -> Response[RpcEnvelope]:
    """Archive the exact persisted thread.

    Args:
        payload: Exact target, cwd, thread ID, and required dispatch receipt key.
    """
    return _thread_control("archive_thread", payload, "thread/archive", {})


@action(is_consequential=True)
def unarchive_thread(payload: ThreadMutationRequest) -> Response[RpcEnvelope]:
    """Unarchive the exact persisted thread.

    Args:
        payload: Exact target, cwd, thread ID, and required dispatch receipt key.
    """
    return _thread_control("unarchive_thread", payload, "thread/unarchive", {})


@action(is_consequential=True)
def delete_thread(payload: ThreadMutationRequest) -> Response[RpcEnvelope]:
    """Delete the exact persisted thread.

    Args:
        payload: Exact target, cwd, thread ID, and required dispatch receipt key.
    """
    return _thread_control("delete_thread", payload, "thread/delete", {})


@action(is_consequential=True)
def set_thread_name(payload: ThreadNameSetRequest) -> Response[RpcEnvelope]:
    """Set the user-facing name of one exact persisted thread.

    Args:
        payload: Exact target, cwd, thread ID, name, and receipt key.
    """
    return _thread_control(
        "set_thread_name", payload, "thread/name/set", {"name": payload.name}
    )


@action(is_consequential=True)
def update_thread_metadata(
    payload: ThreadMetadataUpdateRequest,
) -> Response[RpcEnvelope]:
    """Patch Git metadata without changing omitted fields.

    Args:
        payload: Exact thread identity and at least one typed metadata field.
    """
    params: dict[str, Any] = {
        "gitInfo": {
            native: getattr(payload.git_info, field)
            for field, native in (
                ("sha", "sha"),
                ("branch", "branch"),
                ("origin_url", "originUrl"),
            )
            if field in payload.git_info.model_fields_set
        }
    }
    return _thread_control(
        "update_thread_metadata", payload, "thread/metadata/update", params
    )


@action(is_consequential=True)
def revert_thread(payload: ThreadRevertRequest) -> Response[RpcEnvelope]:
    """Revert persisted history to the prefix before one native turn ID.

    Args:
        payload: Exact thread identity and the native before-turn identifier.
    """
    return _thread_control(
        "revert_thread",
        payload,
        "thread/revert",
        {"beforeTurnId": _action_id(payload.before_turn_id, "before_turn_id")},
    )


@action(is_consequential=True)
def compact_thread(payload: ThreadMutationRequest) -> Response[RpcEnvelope]:
    """Request native compaction for the exact persisted thread.

    Args:
        payload: Exact target, cwd, thread ID, and required dispatch receipt key.
    """
    return _thread_control("compact_thread", payload, "thread/compact/start", {})


@action(is_consequential=True)
def start_review(payload: ReviewStartRequest) -> Response[RpcEnvelope]:
    """Start an inline native review on one exact thread.

    Args:
        payload: Exact thread identity, review target, and required receipt key.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    params = {
        "threadId": thread_id,
        "target": payload.review_target.model_dump(exclude_none=True),
    }

    def invoke(client, receipt):
        _read_guarded_thread(client, thread_id, cwd)
        result = client.request("review/start", params)
        turn = result.get("turn") if isinstance(result, dict) else None
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if (
            result.get("reviewThreadId") != thread_id
            or not isinstance(turn_id, str)
            or not turn_id
        ):
            raise RpcError("Native review response identity mismatch")
        receipt.update(state="accepted", thread_id=thread_id, turn_id=turn_id)
        return result

    return _dispatch_run("start_review", payload, invoke)


@action(is_consequential=False)
def read_account_rate_limits(
    payload: AccountRateLimitsRequest,
) -> Response[RpcEnvelope]:
    """Read native account rate-limit status without consuming credits.

    Args:
        payload: The configured target.
    """
    return _run(
        "read_account_rate_limits",
        payload.target,
        lambda client: client.request("account/rateLimits/read", {}),
    )


@action(is_consequential=False)
def read_account_usage(payload: AccountUsageRequest) -> Response[RpcEnvelope]:
    """Read account usage or a thread-scoped native usage estimate.

    Args:
        payload: Target and optional exact thread/CWD identity.
    """
    params = {}
    if payload.thread_id is not None:
        thread_id = _action_id(payload.thread_id, "thread_id")
        cwd = _action_cwd(payload.cwd)
        params["threadId"] = thread_id

        def invoke(client):
            _read_guarded_thread(client, thread_id, cwd)
            return client.request("account/usage/read", params)

    else:

        def invoke(client):
            return client.request("account/usage/read", params)

    return _run("read_account_usage", payload.target, invoke)


@action(is_consequential=False)
def list_skills(payload: CwdInventoryRequest) -> Response[RpcEnvelope]:
    """List skills discovered for one exact worktree.

    Args:
        payload: Configured target and exact absolute worktree path.
    """
    cwd = _action_cwd(payload.cwd)
    return _run(
        "list_skills",
        payload.target,
        lambda client: client.request("skills/list", {"cwds": [cwd]}),
    )


@action(is_consequential=False)
def list_hooks(payload: CwdInventoryRequest) -> Response[RpcEnvelope]:
    """List hooks discovered for one exact worktree.

    Args:
        payload: Configured target and exact absolute worktree path.
    """
    cwd = _action_cwd(payload.cwd)
    return _run(
        "list_hooks",
        payload.target,
        lambda client: client.request("hooks/list", {"cwds": [cwd]}),
    )


@action(is_consequential=False)
def list_plugins(payload: CwdInventoryRequest) -> Response[RpcEnvelope]:
    """List native plugins associated with one exact worktree.

    Args:
        payload: Configured target and exact absolute worktree path.
    """
    cwd = _action_cwd(payload.cwd)
    return _run(
        "list_plugins",
        payload.target,
        lambda client: client.request("plugin/list", {"cwds": [cwd]}),
    )


@action(is_consequential=False)
def read_plugin(payload: PluginReadRequest) -> Response[RpcEnvelope]:
    """Read one named plugin from the native plugin catalog.

    Args:
        payload: Configured target and exact native plugin name.
    """
    return _run(
        "read_plugin",
        payload.target,
        lambda client: _bounded_native_result(
            client.request("plugin/read", {"pluginName": payload.plugin_name})
        ),
        include_receipts=False,
    )


@action(is_consequential=False)
def list_apps(payload: AppsListRequest) -> Response[RpcEnvelope]:
    """List a bounded page of native apps/connectors.

    Args:
        payload: Target and bounded pagination; optional exact thread/CWD scope.
    """
    params: dict[str, Any] = {"limit": payload.limit}
    if payload.cursor is not None:
        params["cursor"] = payload.cursor
    if payload.thread_id is not None:
        thread_id = _action_id(payload.thread_id, "thread_id")
        cwd = _action_cwd(payload.cwd)
        params["threadId"] = thread_id

        def invoke(client):
            _read_guarded_thread(client, thread_id, cwd)
            return client.request("app/list", params)

    else:

        def invoke(client):
            return client.request("app/list", params)

    return _run("list_apps", payload.target, invoke)


@action(is_consequential=False)
def read_app(payload: AppReadRequest) -> Response[RpcEnvelope]:
    """Read bounded native metadata for one exact app identifier.

    Args:
        payload: Target, exact app ID, optional tool summaries, and optional thread/CWD.
    """
    app_id = _action_id(payload.app_id, "app_id")
    params: dict[str, Any] = {"appIds": [app_id]}
    if payload.include_tools:
        params["includeTools"] = True
    if payload.thread_id is not None:
        thread_id = _action_id(payload.thread_id, "thread_id")
        cwd = _action_cwd(payload.cwd)
        params["threadId"] = thread_id

        def invoke(client):
            _read_guarded_thread(client, thread_id, cwd)
            result = client.request("app/read", params)
            _validate_app_read_response(result, app_id)
            return _bounded_native_result(result)

    else:

        def invoke(client):
            result = client.request("app/read", params)
            _validate_app_read_response(result, app_id)
            return _bounded_native_result(result)

    return _run("read_app", payload.target, invoke)


@action(is_consequential=False)
def read_mcp_resource(payload: McpResourceReadRequest) -> Response[RpcEnvelope]:
    """Read one named native MCP resource with exact thread/CWD scope.

    Args:
        payload: Target, thread identity, server name, and resource URI.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    params = {
        "threadId": thread_id,
        "server": payload.server,
        "uri": payload.uri,
    }

    def invoke(client):
        _read_guarded_thread(client, thread_id, cwd)
        return _bounded_native_result(client.request("mcpServer/resource/read", params))

    return _run("read_mcp_resource", payload.target, invoke, include_receipts=False)


@action(is_consequential=True)
def call_mcp_tool(payload: McpToolCallRequest) -> Response[RpcEnvelope]:
    """Call one explicitly named native MCP tool under Codex's native authority.

    Args:
        payload: Exact thread, server/tool names, bounded arguments, and receipt key.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    params: dict[str, Any] = {
        "threadId": thread_id,
        "server": payload.server,
        "tool": payload.tool,
    }
    if payload.arguments is not None:
        params["arguments"] = payload.arguments

    def invoke(client, receipt):
        _read_guarded_thread(client, thread_id, cwd)
        result = _bounded_native_result(client.request("mcpServer/tool/call", params))
        receipt.update(state="accepted", thread_id=thread_id)
        return result

    return _dispatch_run("call_mcp_tool", payload, invoke)


@action(is_consequential=False)
def list_background_terminals(
    payload: ThreadBackgroundTerminalsListRequest,
) -> Response[RpcEnvelope]:
    """List a bounded page of terminals attached to one exact thread/worktree.

    Args:
        payload: Target, exact thread/CWD identity, and pagination controls.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    params = {"threadId": thread_id, "limit": payload.limit}
    if payload.cursor is not None:
        params["cursor"] = payload.cursor

    def invoke(client):
        _read_guarded_thread(client, thread_id, cwd)
        result = client.request("thread/backgroundTerminals/list", params)
        page = result.get("data") if isinstance(result, dict) else None
        if not isinstance(page, list):
            raise RpcError("Native terminal list response is missing its data page")
        selected = []
        for terminal in page:
            if not isinstance(terminal, dict) or not isinstance(
                terminal.get("cwd"), str
            ):
                raise RpcError("Native terminal result is missing its CWD")
            if terminal["cwd"] == cwd:
                selected.append(terminal)
        return {
            "data": selected,
            "nextCursor": result.get("nextCursor"),
        }

    return _run(
        "list_background_terminals",
        payload.target,
        invoke,
        include_receipts=False,
    )


@action(is_consequential=True)
def terminate_background_terminal(
    payload: ThreadBackgroundTerminalTerminateRequest,
) -> Response[RpcEnvelope]:
    """Terminate a listed native terminal only after exact identity reconciliation.

    Args:
        payload: Exact thread/CWD, process ID, and required receipt key.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)

    def invoke(client, receipt):
        _read_guarded_thread(client, thread_id, cwd)
        page = client.request(
            "thread/backgroundTerminals/list",
            {"threadId": thread_id, "limit": 100},
        )
        terminals = page.get("data") if isinstance(page, dict) else None
        if not isinstance(terminals, list):
            raise RpcError("Native terminal list response is missing its data page")
        matches = [
            terminal
            for terminal in terminals
            if isinstance(terminal, dict)
            and terminal.get("processId") == payload.process_id
            and terminal.get("cwd") == cwd
            and isinstance(terminal.get("itemId"), str)
            and terminal["itemId"]
        ]
        if len(matches) != 1:
            raise RpcError(
                "Native terminal process identity is not unique in the bounded page"
            )
        result = client.request(
            "thread/backgroundTerminals/terminate",
            {"threadId": thread_id, "processId": payload.process_id},
        )
        if not isinstance(result, dict) or result.get("terminated") is not True:
            raise RpcError("Native terminal termination was not confirmed")
        receipt.update(state="accepted", thread_id=thread_id)
        return result

    return _dispatch_run("terminate_background_terminal", payload, invoke)


@action(is_consequential=False)
def list_thread_attachments(
    payload: ThreadAttachmentListRequest,
) -> Response[RpcEnvelope]:
    """List a bounded attachment page from one exact thread.

    Args:
        payload: Exact target, cwd, thread ID, and pagination controls.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    params = {"threadId": thread_id, "limit": payload.limit}
    if payload.cursor is not None:
        params["cursor"] = payload.cursor

    def invoke(client):
        _read_guarded_thread(client, thread_id, cwd)
        return client.request("thread/attachment/list", params)

    return _run("list_thread_attachments", payload.target, invoke)


@action(is_consequential=True)
def add_thread_attachment(
    payload: ThreadAttachmentAddRequest,
) -> Response[RpcEnvelope]:
    """Create or locate one bounded, typed thread attachment.

    Args:
        payload: Exact thread identity, attachment type/key, bounded JSON payload,
            and required dispatch receipt key.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    params = {
        "threadId": thread_id,
        "attachmentType": payload.attachment_type,
        "identityKey": payload.identity_key,
        "payload": payload.payload,
    }

    def invoke(client, receipt):
        _read_guarded_thread(client, thread_id, cwd)
        result = client.request("thread/attachment/add", params)
        attachment = result.get("attachment") if isinstance(result, dict) else None
        if (
            not isinstance(attachment, dict)
            or attachment.get("attachmentType") != payload.attachment_type
            or attachment.get("identityKey") != payload.identity_key
            or not isinstance(attachment.get("id"), str)
            or not attachment["id"]
        ):
            raise RpcError("Native attachment response identity mismatch")
        receipt.update(state="accepted", thread_id=thread_id)
        return result

    return _dispatch_run("add_thread_attachment", payload, invoke)


@action(is_consequential=True)
def remove_thread_attachment(
    payload: ThreadAttachmentRemoveRequest,
) -> Response[RpcEnvelope]:
    """Remove one thread attachment by its exact stable identity key.

    Args:
        payload: Exact thread, attachment type/key, and receipt key.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    params = {
        "threadId": thread_id,
        "attachmentType": payload.attachment_type,
        "identityKey": payload.identity_key,
    }

    def invoke(client, receipt):
        _read_guarded_thread(client, thread_id, cwd)
        result = client.request("thread/attachment/remove", params)
        receipt.update(state="accepted", thread_id=thread_id)
        return result

    return _dispatch_run("remove_thread_attachment", payload, invoke)


@action(is_consequential=True)
def inject_thread_items(payload: ThreadInjectItemsRequest) -> Response[RpcEnvelope]:
    """Append a bounded list of typed user-text message items to one thread.

    Args:
        payload: Exact thread identity, restricted input-text items, and receipt key.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    params = {
        "threadId": thread_id,
        "items": [item.model_dump() for item in payload.items],
    }

    def invoke(client, receipt):
        _read_guarded_thread(client, thread_id, cwd)
        result = client.request("thread/inject_items", params)
        receipt.update(state="accepted", thread_id=thread_id)
        return result

    return _dispatch_run("inject_thread_items", payload, invoke)


@action(is_consequential=False)
def list_thread_sections(payload: ThreadSectionListRequest) -> Response[RpcEnvelope]:
    """List a bounded page of native thread sections for one configured target.

    Args:
        payload: Target and bounded pagination controls.
    """
    params = {}
    if payload.limit is not None:
        params["limit"] = payload.limit
    if payload.cursor is not None:
        params["cursor"] = payload.cursor
    return _run(
        "list_thread_sections",
        payload.target,
        lambda client: client.request("threadSection/list", params),
    )


@action(is_consequential=False)
def search_threads(payload: ThreadSearchRequest) -> Response[RpcEnvelope]:
    """Search a bounded page of threads on one target using the pinned experimental API.

    Args:
        payload: Target, non-empty search text, and bounded native search filters.
    """
    params: dict[str, Any] = {
        "searchTerm": payload.search_term,
        "limit": payload.limit,
    }
    for key, value in (
        ("cursor", payload.cursor),
        ("sortKey", payload.sort_key),
        ("sortDirection", payload.sort_direction),
        ("sourceKinds", payload.source_kinds),
        ("archived", payload.archived),
    ):
        if value is not None:
            params[key] = value

    cwd = _action_cwd(payload.cwd)

    def invoke(client):
        result = client.request("thread/search", params)
        page = result.get("data") if isinstance(result, dict) else None
        if not isinstance(page, list):
            raise RpcError("Native thread/search response is missing its data page")
        selected = []
        for entry in page:
            thread = entry.get("thread") if isinstance(entry, dict) else None
            if (
                not isinstance(thread, dict)
                or not isinstance(thread.get("id"), str)
                or not thread["id"]
                or not isinstance(thread.get("cwd"), str)
            ):
                raise RpcError("Native thread/search result is missing thread identity")
            if thread["cwd"] == cwd:
                selected.append(entry)
        return {
            "data": selected,
            "nextCursor": result.get("nextCursor"),
            "backwardsCursor": result.get("backwardsCursor"),
        }

    return _run(
        "search_threads",
        payload.target,
        invoke,
        include_receipts=False,
    )


@action(is_consequential=False)
def search_thread_occurrences(
    payload: ThreadSearchOccurrencesRequest,
) -> Response[RpcEnvelope]:
    """Find bounded visible-message matches within one exact thread.

    Args:
        payload: Exact target, cwd, thread ID, query, and pagination controls.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    params = {
        "threadId": thread_id,
        "searchTerm": payload.search_term,
        "limit": payload.limit,
    }
    if payload.cursor is not None:
        params["cursor"] = payload.cursor

    def invoke(client):
        _read_guarded_thread(client, thread_id, cwd)
        return client.request("thread/searchOccurrences", params)

    return _run("search_thread_occurrences", payload.target, invoke)


@action(is_consequential=False)
def list_thread_timeline(payload: ThreadTimelineListRequest) -> Response[RpcEnvelope]:
    """Read a bounded native timeline page for one exact thread.

    Args:
        payload: Exact target, cwd, thread ID, and pagination controls.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    params = {"threadId": thread_id, "limit": payload.limit}
    if payload.cursor is not None:
        params["cursor"] = payload.cursor

    def invoke(client):
        _read_guarded_thread(client, thread_id, cwd)
        return client.request("thread/timeline/list", params)

    return _run("list_thread_timeline", payload.target, invoke)


@action(is_consequential=False)
def list_thread_queue(payload: ThreadQueueListRequest) -> Response[RpcEnvelope]:
    """List a bounded native queue page for one exact thread.

    Args:
        payload: Exact target, cwd, thread ID, and page controls.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    params = {"threadId": thread_id, "limit": payload.limit}
    if payload.cursor is not None:
        params["cursor"] = payload.cursor

    def invoke(client):
        _read_guarded_thread(client, thread_id, cwd)
        return client.request("thread/queue/list", params)

    return _run("list_thread_queue", payload.target, invoke)


@action(is_consequential=True)
def add_thread_queue_item(payload: ThreadQueueAddRequest) -> Response[RpcEnvelope]:
    """Add one bounded text submission to the exact native thread queue.

    Args:
        payload: Exact thread identity, client message ID, text, and receipt key.
    """
    return _thread_queue_control(
        "add_thread_queue_item",
        payload,
        "thread/queue/add",
        {
            "input": [{"type": "text", "text": payload.text}],
            "clientUserMessageId": payload.client_user_message_id,
        },
    )


@action(is_consequential=True)
def update_thread_queue_item(
    payload: ThreadQueueUpdateRequest,
) -> Response[RpcEnvelope]:
    """Replace the typed text input for one exact queued submission.

    Args:
        payload: Exact thread and queue item identities, text, and receipt key.
    """
    return _thread_queue_control(
        "update_thread_queue_item",
        payload,
        "thread/queue/update",
        {
            "queuedSubmissionId": payload.queued_submission_id,
            "input": [{"type": "text", "text": payload.text}],
        },
        expected_submission_id=payload.queued_submission_id,
    )


@action(is_consequential=True)
def delete_thread_queue_item(
    payload: ThreadQueueDeleteRequest,
) -> Response[RpcEnvelope]:
    """Delete one exact queued submission.

    Args:
        payload: Exact thread and queue item identities, plus receipt key.
    """
    return _thread_queue_control(
        "delete_thread_queue_item",
        payload,
        "thread/queue/delete",
        {"queuedSubmissionId": payload.queued_submission_id},
    )


@action(is_consequential=True)
def reorder_thread_queue(
    payload: ThreadQueueReorderRequest,
) -> Response[RpcEnvelope]:
    """Set the order of a bounded set of unique queued submission IDs.

    Args:
        payload: Exact thread identity and an ordered, unique ID list.
    """
    return _thread_queue_control(
        "reorder_thread_queue",
        payload,
        "thread/queue/reorder",
        {"queuedSubmissionIds": payload.queued_submission_ids},
    )


@action(is_consequential=True)
def start_thread_queue(payload: ThreadQueueStartRequest) -> Response[RpcEnvelope]:
    """Start one exact queued submission or the native queue head.

    Args:
        payload: Exact thread identity and optional queued item ID.
    """
    params = (
        {"queuedSubmissionId": payload.queued_submission_id}
        if payload.queued_submission_id is not None
        else {}
    )
    return _thread_queue_control(
        "start_thread_queue",
        payload,
        "thread/queue/start",
        params,
        starts_turn=True,
    )


@action(is_consequential=True)
def create_thread_section(
    payload: ThreadSectionCreateRequest,
) -> Response[RpcEnvelope]:
    """Create one named native thread section.

    Args:
        payload: Configured target, bounded section name, and receipt key.
    """

    def invoke(client, receipt):
        result = client.request("threadSection/create", {"name": payload.name})
        section = result.get("section") if isinstance(result, dict) else None
        if (
            not isinstance(section, dict)
            or not isinstance(section.get("id"), str)
            or not section["id"]
            or section.get("name") != payload.name
        ):
            raise RpcError("Native section creation response identity mismatch")
        receipt.update(state="accepted")
        return result

    return _dispatch_run("create_thread_section", payload, invoke)


@action(is_consequential=True)
def update_thread_section(
    payload: ThreadSectionUpdateRequest,
) -> Response[RpcEnvelope]:
    """Rename one exact native thread section.

    Args:
        payload: Configured target, stable section ID, name, and receipt key.
    """

    def invoke(client, receipt):
        result = client.request(
            "threadSection/update",
            {"sectionId": payload.section_id, "name": payload.name},
        )
        section = result.get("section") if isinstance(result, dict) else None
        if (
            not isinstance(section, dict)
            or section.get("id") != payload.section_id
            or section.get("name") != payload.name
        ):
            raise RpcError("Native section update response identity mismatch")
        receipt.update(state="accepted")
        return result

    return _dispatch_run("update_thread_section", payload, invoke)


@action(is_consequential=True)
def delete_thread_section(
    payload: ThreadSectionDeleteRequest,
) -> Response[RpcEnvelope]:
    """Delete one exact native thread section by its stable ID.

    Args:
        payload: Configured target, stable section ID, and receipt key.
    """

    def invoke(client, receipt):
        result = client.request(
            "threadSection/delete", {"sectionId": payload.section_id}
        )
        receipt.update(state="accepted")
        return result

    return _dispatch_run("delete_thread_section", payload, invoke)


@action(is_consequential=True)
def move_thread_to_section(
    payload: ThreadSectionMoveRequest,
) -> Response[RpcEnvelope]:
    """Move one exact thread into a section or remove it from its section.

    Args:
        payload: Exact thread/CWD, destination section, optional insertion anchor, and receipt ID.
    """
    thread_id = _action_id(payload.thread_id, "thread_id")
    cwd = _action_cwd(payload.cwd)
    section_id = (
        _action_id(payload.section_id, "section_id")
        if payload.section_id is not None
        else None
    )
    before_thread_id = (
        _action_id(payload.before_thread_id, "before_thread_id")
        if payload.before_thread_id is not None
        else None
    )
    if before_thread_id == thread_id:
        raise ActionError("before_thread_id must differ from thread_id")
    params: dict[str, Any] = {"threadId": thread_id, "sectionId": section_id}
    if before_thread_id is not None:
        params["beforeThreadId"] = before_thread_id

    def invoke(client, receipt):
        _read_guarded_thread(client, thread_id, cwd)
        if before_thread_id is not None:
            _read_guarded_thread(client, before_thread_id, cwd)
        if section_id is not None:
            sections = client.request("threadSection/list", {"limit": 100})
            page = sections.get("data") if isinstance(sections, dict) else None
            if not isinstance(page, list):
                raise RpcError("Native section list is missing its data page")
            if not any(
                isinstance(section, dict) and section.get("id") == section_id
                for section in page
            ):
                if sections.get("nextCursor") is not None:
                    raise RpcError(
                        "Native destination section was not found in the bounded first page"
                    )
                raise ActionError("Destination section does not exist on this target")
        result = client.request("thread/section/move", params)
        if not isinstance(result, dict):
            raise RpcError("Native section move response is not an object")
        _bounded_native_result(result)
        receipt.update(state="accepted", thread_id=thread_id)
        return result

    return _dispatch_run("move_thread_to_section", payload, invoke)


@action(is_consequential=False)
def list_thread_turns(payload: ThreadTurnsListRequest) -> Response[RpcEnvelope]:
    """Read one page of turns for an exact target-scoped workstream thread.

    Args:
        payload: Target, exact cwd/thread, and bounded turn-page options.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")
    params = _optional_page_params(payload)
    if payload.items_view is not None:
        params["itemsView"] = payload.items_view

    def invoke(client):
        _guard_cwd(client, thread_id, cwd)
        return client.request("thread/turns/list", params)

    return _run("thread/turns/list", payload.target, invoke)


@action(is_consequential=False)
def list_thread_items(payload: ThreadItemsListRequest) -> Response[RpcEnvelope]:
    """Read one page of items for an exact target-scoped workstream thread.

    Args:
        payload: Target, exact cwd/thread, optional turn, and item-page options.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")
    params = _optional_page_params(payload)
    if payload.turn_id is not None:
        params["turnId"] = _action_id(payload.turn_id, "turn_id")

    def invoke(client):
        _guard_cwd(client, thread_id, cwd)
        return client.request("thread/items/list", params)

    return _run("thread/items/list", payload.target, invoke)


@action(is_consequential=False)
def read_thread(payload: ThreadReadRequest) -> Response[RpcEnvelope]:
    """Read one target-scoped thread without changing it.

    Args:
        payload: Target, exact worktree cwd, and thread identifier.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")

    def invoke(client):
        result = client.request(
            "thread/read",
            {
                "threadId": thread_id,
                "includeTurns": payload.include_turns,
            },
        )
        thread = result.get("thread") if isinstance(result, dict) else None
        if (
            not isinstance(thread, dict)
            or thread.get("id") != thread_id
            or thread.get("cwd") != cwd
        ):
            raise ActionError(
                "Stored thread id/cwd does not match the requested workstream"
            )
        return result

    return _run("thread/read", payload.target, invoke)


@action(is_consequential=False)
def get_thread_snapshot(payload: ThreadSnapshotRequest) -> Response[RpcEnvelope]:
    """Return a byte-bounded native status projection for an exact thread/worktree.

    Args:
        payload: Target, exact CWD/thread identity, and optional prior revision.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")

    def invoke(client):
        response = client.request(
            "thread/read", {"threadId": thread_id, "includeTurns": False}
        )
        thread = response.get("thread") if isinstance(response, dict) else None
        if (
            not isinstance(thread, dict)
            or thread.get("id") != thread_id
            or thread.get("cwd") != cwd
            or not isinstance(thread.get("turns"), list)
            or thread["turns"]
        ):
            raise RpcError("Native snapshot thread/CWD identity mismatch")

        turns_response = client.request(
            "thread/turns/list",
            {
                "threadId": thread_id,
                "limit": 1,
                "sortDirection": "desc",
                "itemsView": "notLoaded",
            },
        )
        turns = turns_response.get("data") if isinstance(turns_response, dict) else None
        if not isinstance(turns, list) or len(turns) > 1:
            raise RpcError("Native snapshot turn page is invalid")
        turn = turns[0] if turns else None
        if turn is not None and (
            not isinstance(turn, dict)
            or not isinstance(turn.get("id"), str)
            or not turn["id"]
            or len(turn["id"]) > 512
            or turn.get("items") != []
            or turn.get("itemsView") != "notLoaded"
        ):
            raise RpcError("Native snapshot turn identity is invalid")

        latest_item = None
        item_cursor = None
        if turn is not None:
            items_response = client.request(
                "thread/items/list",
                {
                    "threadId": thread_id,
                    "turnId": turn["id"],
                    "limit": 1,
                    "sortDirection": "desc",
                },
            )
            items = (
                items_response.get("data") if isinstance(items_response, dict) else None
            )
            item_cursor = (
                items_response.get("nextCursor")
                if isinstance(items_response, dict)
                else None
            )
            if (
                not isinstance(items, list)
                or len(items) > 1
                or (item_cursor is not None and not isinstance(item_cursor, str))
            ):
                raise RpcError("Native snapshot item page is invalid")
            latest_item = items[0] if items else None

        projected = _project_thread_snapshot(
            payload.target, cwd, thread_id, thread, turn, latest_item, item_cursor
        )
        observed_at = (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        snapshot = _snapshot_response(projected, observed_at)
        if payload.revision == snapshot["revision"]:
            snapshot = {
                "target": payload.target,
                "cwd": cwd,
                "thread_id": thread_id,
                "observed_at": observed_at,
                "revision": snapshot["revision"],
                "changed": False,
                "native_state_authoritative": True,
                "thread_status": snapshot["thread_status"],
                "active_flags": snapshot["active_flags"],
                "latest_turn": snapshot["latest_turn"],
            }
        return snapshot

    return _run(
        "get_thread_snapshot",
        payload.target,
        invoke,
        include_receipts=False,
        include_events=False,
        maximum_response_bytes=SNAPSHOT_MAX_BYTES,
    )


@action(is_consequential=True)
def update_thread_settings(
    payload: ThreadSettingsUpdateRequest,
) -> Response[RpcEnvelope]:
    """Update only model or reasoning effort for subsequent thread turns.

    Args:
        payload: Target, exact cwd/thread, and model or effort for later turns.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")

    def invoke(client):
        _guard_cwd(client, thread_id, cwd)
        params: dict[str, Any] = {"threadId": thread_id}
        if payload.model is not None:
            params["model"] = payload.model
        if payload.effort is not None:
            params["effort"] = payload.effort
        updated = client.request("thread/settings/update", params)
        current = _read_guarded_thread(client, thread_id, cwd)
        return {"updated": updated, "thread": current["thread"]}

    return _run("thread/settings/update", payload.target, invoke)


@action(is_consequential=True)
def update_turn_settings(payload: TurnSettingsUpdateRequest) -> Response[RpcEnvelope]:
    """Apply native model or effort settings to one explicitly active turn.

    Args:
        payload: Target, exact cwd/thread/active-turn, and model or effort.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")
    turn_id = _action_id(payload.turn_id, "turn_id")

    def invoke(client):
        _guard_cwd(client, thread_id, cwd)
        params: dict[str, Any] = {"threadId": thread_id, "turnId": turn_id}
        if payload.model is not None:
            params["model"] = payload.model
        if payload.effort is not None:
            params["effort"] = payload.effort
        return client.request("turn/settings/update", params)

    return _run("turn/settings/update", payload.target, invoke)


def _goal_result(
    result: dict[str, Any], thread_id: str, *, required: bool = False
) -> dict[str, Any]:
    goal = result.get("goal") if isinstance(result, dict) else None
    if (required and not isinstance(goal, dict)) or (
        goal is not None
        and (not isinstance(goal, dict) or goal.get("threadId") != thread_id)
    ):
        raise ActionError("Native goal thread id does not match the requested thread")
    return result


@action(is_consequential=False)
def get_thread_goal(payload: ThreadGoalGetRequest) -> Response[RpcEnvelope]:
    """Read the native coordinator goal for one exact workstream thread.

    Args:
        payload: Target and exact worktree cwd/thread whose goal is read.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")

    def invoke(client):
        _guard_cwd(client, thread_id, cwd)
        return _goal_result(
            client.request("thread/goal/get", {"threadId": thread_id}), thread_id
        )

    return _run("thread/goal/get", payload.target, invoke)


@action(is_consequential=True)
def set_thread_goal(payload: ThreadGoalSetRequest) -> Response[RpcEnvelope]:
    """Set bounded native coordinator-goal fields for one exact thread.

    Args:
        payload: Target, exact cwd/thread, and objective, status, or token budget.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")

    def invoke(client):
        _guard_cwd(client, thread_id, cwd)
        params: dict[str, Any] = {"threadId": thread_id}
        if payload.objective is not None:
            params["objective"] = payload.objective
        if payload.status is not None:
            params["status"] = payload.status
        if payload.token_budget is not None:
            params["tokenBudget"] = payload.token_budget
        return _goal_result(
            client.request("thread/goal/set", params), thread_id, required=True
        )

    return _run("thread/goal/set", payload.target, invoke)


@action(is_consequential=True)
def clear_thread_goal(payload: ThreadGoalClearRequest) -> Response[RpcEnvelope]:
    """Clear the native coordinator goal for one exact workstream thread.

    Args:
        payload: Target and exact worktree cwd/thread whose goal is cleared.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")

    def invoke(client):
        _guard_cwd(client, thread_id, cwd)
        return client.request("thread/goal/clear", {"threadId": thread_id})

    return _run("thread/goal/clear", payload.target, invoke)


@action(is_consequential=False)
def list_models(payload: ModelListRequest) -> Response[RpcEnvelope]:
    """List the native model catalog without changing thread configuration.

    Args:
        payload: Target and optional bounded model-catalog page options.
    """
    params: dict[str, Any] = {}
    if payload.limit is not None:
        params["limit"] = payload.limit
    if payload.cursor is not None:
        params["cursor"] = payload.cursor
    if payload.include_hidden is not None:
        params["includeHidden"] = payload.include_hidden
    return _run(
        "model/list",
        payload.target,
        lambda client: client.request("model/list", params),
    )


@action(is_consequential=False)
def read_model_provider_capabilities(
    payload: ModelProviderCapabilitiesRequest,
) -> Response[RpcEnvelope]:
    """Read native provider capabilities for the selected target.

    Args:
        payload: Configured target whose native provider capabilities are read.
    """
    return _run(
        "modelProvider/capabilities/read",
        payload.target,
        lambda client: client.request("modelProvider/capabilities/read", {}),
    )


@action(is_consequential=False)
def read_server_diagnostics(payload: ServerDiagnosticsRequest) -> Response[RpcEnvelope]:
    """Read bounded native server process and gauge diagnostics.

    Args:
        payload: Configured target whose bounded native diagnostics are read.
    """
    return _run(
        "server/diagnostics",
        payload.target,
        lambda client: client.request("server/diagnostics", {}),
    )


@action(is_consequential=False)
def list_mcp_server_status(
    payload: McpServerStatusListRequest,
) -> Response[RpcEnvelope]:
    """List native MCP status with optional bounded inventory detail.

    Args:
        payload: Target, optional thread, bounded page, and inventory detail.
    """
    params: dict[str, Any] = {}
    if payload.thread_id is not None:
        params["threadId"] = _action_id(payload.thread_id, "thread_id")
    if payload.limit is not None:
        params["limit"] = payload.limit
    if payload.cursor is not None:
        params["cursor"] = payload.cursor
    if payload.detail is not None:
        params["detail"] = payload.detail
    return _run(
        "mcpServerStatus/list",
        payload.target,
        lambda client: client.request("mcpServerStatus/list", params),
    )


@action(is_consequential=False)
def list_loaded_threads(payload: LoadedThreadListRequest) -> Response[RpcEnvelope]:
    """List native thread ids currently loaded in memory.

    Args:
        payload: Target and optional bounded loaded-thread page options.
    """
    params: dict[str, Any] = {}
    if payload.limit is not None:
        params["limit"] = payload.limit
    if payload.cursor is not None:
        params["cursor"] = payload.cursor
    return _run(
        "thread/loaded/list",
        payload.target,
        lambda client: client.request("thread/loaded/list", params),
    )


@action(is_consequential=True)
def start_thread(payload: ThreadStartRequest) -> Response[RpcEnvelope]:
    """Create a native thread rooted at the explicit absolute worktree path.

    Args:
        payload: Target and explicit absolute worktree cwd.
    """
    cwd = _action_cwd(payload.cwd)

    def invoke(client, receipt):
        params = {"cwd": cwd, **_optional_thread_settings(payload)}
        result = client.request("thread/start", params)
        thread = result.get("thread") if isinstance(result, dict) else None
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise ActionError("Native thread/start returned no thread id")
        receipt.update(thread_id=thread_id)
        client.set_workstream(cwd, thread_id)
        receipt.update(state="created_not_materialized")
        return _apply_thread_effort(client, result, thread_id, cwd, payload.effort)

    return _dispatch_run("thread/start", payload, invoke)


@action(is_consequential=True)
def create_thread_and_start_turn(
    payload: CreateThreadAndStartTurnRequest,
) -> Response[RpcEnvelope]:
    """Create a fresh thread and start its first typed turn on one connection.

    A new Codex 0.153.4 thread has no resumable rollout until its first turn;
    this action keeps creation and that first turn on the native connection.

    Args:
        payload: Target, exact cwd, first input, optional settings, and wait flag.
    """
    cwd = _action_cwd(payload.cwd)

    def invoke(client, receipt):
        params = {"cwd": cwd, **_optional_thread_settings(payload)}
        if payload.enable_list_threads_callback:
            params["dynamicTools"] = [
                {
                    "type": "namespace",
                    "name": "codex_app",
                    "description": "Read-only native state for this exact workstream.",
                    "tools": [
                        {
                            "type": "function",
                            "name": "list_threads",
                            "description": "List native threads filtered to this thread's exact cwd.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "limit": {
                                        "type": "integer",
                                        "minimum": 1,
                                        "maximum": 100,
                                    },
                                    "cursor": {"type": "string", "minLength": 1},
                                    "cwd": {"type": "string", "enum": [cwd]},
                                },
                                "required": ["cwd"],
                                "additionalProperties": False,
                            },
                        }
                    ],
                }
            ]
        created = client.request("thread/start", params)
        thread = created.get("thread") if isinstance(created, dict) else None
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise ActionError("Native thread/start returned no thread id")
        receipt.update(thread_id=thread_id)
        client.set_workstream(cwd, thread_id)
        client.ensure_thread_attached(
            thread_id,
            {"threadId": thread_id, "excludeTurns": True, "cwd": cwd},
        )
        turn_params: dict[str, Any] = {
            "threadId": thread_id,
            "cwd": cwd,
            "input": [{"type": "text", "text": payload.text}],
        }
        if payload.effort is not None:
            turn_params["effort"] = payload.effort
        accepted = client.request("turn/start", turn_params)
        turn_id = accepted.get("turn", {}).get("id")
        if not isinstance(turn_id, str) or not turn_id:
            raise RpcError("Native turn acknowledgement missing ID")
        receipt.update(state="accepted", thread_id=thread_id, turn_id=turn_id)
        result: dict[str, Any] = {"created": created, "accepted": accepted}
        for native_key in ("model", "modelProvider"):
            if native_key in created:
                result[native_key] = created[native_key]
        if not payload.wait_for_completion:
            return result
        turn = accepted.get("turn") if isinstance(accepted, dict) else None
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(turn_id, str) or not turn_id:
            raise ActionError("Native turn/start returned no turn id")
        result["completed"] = client.wait_turn(
            thread_id, turn_id, timeout=payload.wait_seconds
        )
        receipt.update(state=result["completed"].get("status", "completed"))
        current = _read_guarded_thread(client, thread_id, cwd)
        result["thread"] = current["thread"]
        return result

    return _dispatch_run("thread/start + turn/start", payload, invoke)


@action(is_consequential=True)
def resume_thread(payload: ThreadResumeRequest) -> Response[RpcEnvelope]:
    """Attach the current native connection to an existing thread after cwd verification.

    Args:
        payload: Target, exact worktree cwd, and thread identifier.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")

    def invoke(client):
        client.set_workstream(cwd, thread_id)
        _guard_cwd(client, thread_id, cwd)
        params: dict[str, Any] = {
            "threadId": thread_id,
            "cwd": cwd,
            "excludeTurns": True,
            **_optional_thread_settings(payload),
        }
        result = client.request(
            "thread/resume",
            params,
        )
        return _apply_thread_effort(client, result, thread_id, cwd, payload.effort)

    return _run("thread/resume", payload.target, invoke)


@action(is_consequential=True)
def start_turn(payload: TurnStartRequest) -> Response[RpcEnvelope]:
    """Resume, then start a text turn on the same native connection.

    Optional model and effort override native turn settings; the provider and
    execution policy are preserved. Omit them to retain the existing settings.

    Args:
        payload: Target, exact cwd, thread, text, and optional model/effort.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")

    def invoke(client, receipt):
        receipt.update(thread_id=thread_id)
        client.set_workstream(cwd, thread_id)
        _guard_cwd(client, thread_id, cwd)
        client.request("thread/resume", {"threadId": thread_id, "excludeTurns": True})
        params: dict[str, Any] = {
            "threadId": thread_id,
            "cwd": cwd,
            "input": [{"type": "text", "text": payload.text}],
        }
        if payload.model is not None:
            params["model"] = payload.model
        if payload.effort is not None:
            params["effort"] = payload.effort
        accepted = client.request("turn/start", params)
        turn_id = accepted.get("turn", {}).get("id")
        if not isinstance(turn_id, str) or not turn_id:
            raise RpcError("Native turn acknowledgement missing ID")
        receipt.update(state="accepted", thread_id=thread_id, turn_id=turn_id)
        if not payload.wait_for_completion:
            return accepted
        turn_id = accepted["turn"]["id"]
        completed = client.wait_turn(thread_id, turn_id, timeout=payload.wait_seconds)
        receipt.update(state=completed.get("status", "completed"))
        return {"accepted": accepted, "completed": completed}

    return _dispatch_run("turn/start", payload, invoke)


@action(is_consequential=True)
def steer_turn(payload: TurnSteerRequest) -> Response[RpcEnvelope]:
    """Steer only the explicitly named active turn after cwd verification.

    Args:
        payload: Target, exact worktree cwd, thread/turn identifiers, and steer text.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")
    turn_id = _action_id(payload.turn_id, "turn_id")

    def invoke(client):
        _guard_cwd(client, thread_id, cwd)
        return client.request(
            "turn/steer",
            {
                "threadId": thread_id,
                "expectedTurnId": turn_id,
                "input": [{"type": "text", "text": payload.text}],
            },
        )

    return _run("turn/steer", payload.target, invoke)


@action(is_consequential=True)
def interrupt_turn(payload: TurnInterruptRequest) -> Response[RpcEnvelope]:
    """Interrupt only the explicitly named active turn after cwd verification.

    Args:
        payload: Target, exact worktree cwd, and thread/turn identifiers.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")
    turn_id = _action_id(payload.turn_id, "turn_id")

    def invoke(client):
        _guard_cwd(client, thread_id, cwd)
        return client.request(
            "turn/interrupt",
            {
                "threadId": thread_id,
                "turnId": turn_id,
            },
        )

    return _run("turn/interrupt", payload.target, invoke)
