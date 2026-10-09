"""Shared models behavior; importing this module registers no actions."""

from __future__ import annotations

from typing import Annotated
from typing import Any
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from typing import Literal
import json
from pydantic import model_validator


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


class ExecutionPolicyFields(StrictModel):
    approval_policy: Literal["untrusted", "on-request", "never"] | None = Field(
        default=None,
        description="Optional native approval policy for this thread/turn and subsequent turns. Omission preserves native settings. Does not bypass Executor approvals or operator restrictions.",
    )
    sandbox: Literal["read-only", "workspace-write", "danger-full-access"] | None = (
        Field(
            default=None,
            description="Optional native sandbox mode. danger-full-access removes the native sandbox; use only for explicitly authorized workers. Omission preserves native settings.",
        )
    )


class ThreadSettingsFields(ExecutionPolicyFields):
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


class TurnStartRequest(ExecutionPolicyFields):
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
    approval_policy: str | dict[str, Any] | None = None
    sandbox_policy: dict[str, Any] | None = None
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


class DispatchReceiptRequest(StrictModel):
    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
