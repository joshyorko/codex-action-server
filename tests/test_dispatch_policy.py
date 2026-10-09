"""Explicit per-worker native policy, without changing defaults or approval gates."""

from unittest.mock import patch

import pytest
from pydantic import ValidationError
from test_actions import load_actions
from test_control_actions import ControlClient


@pytest.mark.parametrize(
    "name,action,extra,method",
    [
        ("ThreadStartRequest", "start_thread", {}, "thread/start"),
        (
            "ThreadResumeRequest",
            "resume_thread",
            {"thread_id": "thread-1"},
            "thread/resume",
        ),
        (
            "CreateThreadAndStartTurnRequest",
            "create_thread_and_start_turn",
            {"text": "hi"},
            "thread/start",
        ),
        (
            "TurnStartRequest",
            "start_turn",
            {"thread_id": "thread-1", "text": "hi"},
            "turn/start",
        ),
    ],
)
@pytest.mark.parametrize(
    "sandbox,native_type",
    [
        ("danger-full-access", "dangerFullAccess"),
        ("workspace-write", "workspaceWrite"),
        ("read-only", "readOnly"),
    ],
)
def test_explicit_policy_maps_to_native(
    name, action, extra, method, sandbox, native_type
):
    module = load_actions()
    client = ControlClient(None)
    payload = getattr(module, name)(
        target="local",
        cwd="/trusted",
        approval_policy="never",
        sandbox=sandbox,
        **extra,
    )
    with patch.object(module, "Client", return_value=client):
        getattr(module, action)(payload)
    params = next(p for m, p in client.calls if m == method)
    assert params["approvalPolicy"] == "never"
    assert (
        params.get("sandboxPolicy") == {"type": native_type}
        if method == "turn/start"
        else params["sandbox"] == sandbox
    )


@pytest.mark.parametrize(
    "name,extra",
    [
        ("ThreadStartRequest", {}),
        ("ThreadResumeRequest", {"thread_id": "t"}),
        ("CreateThreadAndStartTurnRequest", {"text": "hi"}),
        ("TurnStartRequest", {"thread_id": "t", "text": "hi"}),
    ],
)
@pytest.mark.parametrize(
    "field,value",
    [
        ("approval_policy", "always"),
        ("approval_policy", True),
        ("sandbox", "full-access"),
        ("sandbox", {}),
    ],
)
def test_policy_rejects_invalid_values(name, extra, field, value):
    module = load_actions()
    with pytest.raises(ValidationError):
        getattr(module, name)(target="local", cwd="/trusted", **extra, **{field: value})


def test_effective_policy_uses_native_observation_not_request():
    module = load_actions()
    client = ControlClient(None)
    original = client.request

    def request(method, params):
        result = original(method, params)
        if method == "thread/start":
            result.update(approvalPolicy="on-request", sandbox={"type": "readOnly"})
        return result

    client.request = request
    with patch.object(module, "Client", return_value=client):
        result = module.create_thread_and_start_turn(
            module.CreateThreadAndStartTurnRequest(
                target="local",
                cwd="/trusted",
                text="hi",
                approval_policy="never",
                sandbox="danger-full-access",
            )
        )
    assert result.result.effective_configuration.approval_policy == "on-request"
    assert result.result.effective_configuration.sandbox_policy == {"type": "readOnly"}


def test_policy_change_conflicts_with_existing_dispatch_id(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_ACTION_RECEIPTS", str(tmp_path))
    module = load_actions()
    payload = module.ThreadStartRequest(
        target="local",
        cwd="/trusted",
        request_id="policy-once",
        approval_policy="never",
        sandbox="danger-full-access",
    )
    with patch.object(module, "Client", return_value=ControlClient(None)):
        module.start_thread(payload)
    with patch.object(
        module, "Client", side_effect=AssertionError("must not dispatch")
    ):
        with pytest.raises(RuntimeError, match="dispatch receipt rejected"):
            module.start_thread(
                payload.model_copy(update={"approval_policy": "on-request"})
            )


@pytest.mark.parametrize(
    "name,action,extra",
    [
        ("ThreadStartRequest", "start_thread", {}),
        ("ThreadResumeRequest", "resume_thread", {"thread_id": "thread-1"}),
        (
            "CreateThreadAndStartTurnRequest",
            "create_thread_and_start_turn",
            {"text": "hi"},
        ),
        ("TurnStartRequest", "start_turn", {"thread_id": "thread-1", "text": "hi"}),
    ],
)
def test_omitted_policy_preserves_native_defaults(name, action, extra):
    module = load_actions()
    client = ControlClient(None)
    with patch.object(module, "Client", return_value=client):
        result = getattr(module, action)(
            getattr(module, name)(target="local", cwd="/trusted", **extra)
        )
    for _, params in client.calls:
        assert not {"approvalPolicy", "sandbox", "sandboxPolicy"} & params.keys()
    effective = result.result.effective_configuration
    assert effective is None or (
        effective.approval_policy is None and effective.sandbox_policy is None
    )


def test_turn_override_does_not_claim_unobserved_effective_policy():
    module = load_actions()
    with patch.object(module, "Client", return_value=ControlClient(None)):
        result = module.start_turn(
            module.TurnStartRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                text="hi",
                approval_policy="never",
                sandbox="danger-full-access",
            )
        )
    assert result.result.effective_configuration is None


def test_pre_policy_receipt_replays_without_dispatch(tmp_path, monkeypatch):
    import dispatch_receipts

    monkeypatch.setenv("CODEX_ACTION_RECEIPTS", str(tmp_path))
    module = load_actions()
    payload = module.ThreadStartRequest(
        target="local", cwd="/trusted", request_id="old-receipt"
    )
    old_payload = payload.model_dump(exclude={"approval_policy", "sandbox"})
    with dispatch_receipts.reserve(
        tmp_path, "old-receipt", {"operation": "thread/start", **old_payload}
    ) as receipt:
        receipt.update(thread_id="existing-thread", state="created_not_materialized")
    with patch.object(
        module, "Client", side_effect=AssertionError("must not reconnect")
    ):
        result = module.start_thread(payload)
    assert result.result.result["dispatch"]["replayed"]
    assert result.result.result["dispatch"]["thread_id"] == "existing-thread"
