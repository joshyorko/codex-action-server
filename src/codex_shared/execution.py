"""Shared execution behavior; importing this module registers no actions."""

from __future__ import annotations

from actions import ActionError
from typing import Any
from codex_rpc import Client
from codex_shared.models import CreateThreadAndStartTurnRequest
from actions import Response
from codex_shared.models import ReviewStartRequest
from codex_shared.models import RpcEnvelope
from codex_rpc import RpcError
from codex_shared.models import ThreadGoalClearRequest
from codex_shared.models import ThreadGoalGetRequest
from codex_shared.models import ThreadGoalSetRequest
from codex_shared.models import ThreadResumeRequest
from codex_shared.models import ThreadSettingsUpdateRequest
from codex_shared.models import ThreadStartRequest
from codex_shared.models import TurnInterruptRequest
from codex_shared.models import TurnSettingsUpdateRequest
from codex_shared.models import TurnStartRequest
from codex_shared.models import TurnSteerRequest
from codex_shared.common import _action_cwd
from codex_shared.common import _action_id
from codex_shared.common import _dispatch_run
from codex_shared.common import _guard_cwd
from codex_shared.common import _optional_thread_settings
from codex_shared.common import _optional_turn_policy
from codex_shared.common import _read_guarded_thread
from codex_shared.common import _run


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


def start_turn(payload: TurnStartRequest) -> Response[RpcEnvelope]:
    """Resume, then start a text turn on the same native connection.

    Optional model, effort, approval policy and sandbox override native turn
    settings. Omitted fields preserve existing settings.

    Args:
        payload: Target, exact cwd, thread, text, and optional execution settings.
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
        params.update(_optional_turn_policy(payload))
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
