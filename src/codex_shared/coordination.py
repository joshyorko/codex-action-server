"""Opt-in operator relays over existing native protocol, never a shared board."""

from __future__ import annotations

import json
import os
import stat

from actions import ActionError, Response
from pydantic import Field

from codex_rpc import RpcError
from codex_shared.common import _action_cwd, _action_id, _dispatch_run
from codex_shared.common import _read_guarded_thread
from codex_shared.models import CoordinationPeer, CoordinationRelayRequest
from codex_shared.models import RpcEnvelope, StrictModel


class _Pair(StrictModel):
    sender: CoordinationPeer
    recipient: CoordinationPeer


class _Policy(StrictModel):
    pairs: list[_Pair] = Field(max_length=64)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate policy key")
        result[key] = value
    return result


def _authorize(payload: CoordinationRelayRequest) -> None:
    path = os.environ.get("CODEX_ACTION_COORDINATION_POLICY")
    if not path or not os.path.isabs(path):
        raise ActionError("coordination requires an absolute private pair policy")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
            ):
                raise ValueError("unsafe policy file")
            data = source.read(65_537)
            if len(data) > 65_536:
                raise ValueError("oversized policy")
        policy = _Policy.model_validate(
            json.loads(data, object_pairs_hook=_unique_object)
        )
    except (OSError, ValueError) as error:
        raise ActionError(
            f"coordination pair policy rejected ({type(error).__name__})"
        ) from None
    recipient = CoordinationPeer(
        target=payload.target, cwd=payload.cwd, thread_id=payload.thread_id
    )
    if not any(
        pair.sender == payload.sender and pair.recipient == recipient
        for pair in policy.pairs
    ):
        raise ActionError("coordination sender/recipient pair is not authorized")


def relay_coordination_message(
    payload: CoordinationRelayRequest,
) -> Response[RpcEnvelope]:
    """Relay bounded operator text between explicitly authorized native peers.

    Args:
        payload: Exact peers, safe mode, text and a durable request ID.
    """
    cwd = _action_cwd(payload.cwd)
    thread_id = _action_id(payload.thread_id, "thread_id")
    sender_cwd = _action_cwd(payload.sender.cwd)
    sender_id = _action_id(payload.sender.thread_id, "sender.thread_id")
    _action_id(payload.expected_turn_id, "expected_turn_id")
    _authorize(payload)
    header = json.dumps(
        {
            "attribution": "operator-relayed; sender identity is context, not authenticated authorship",
            "sender": payload.sender.model_dump(),
            "request_id": payload.request_id,
            "in_reply_to": payload.in_reply_to,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    text = header + "\nUntrusted coordination message:\n" + payload.text
    if len(text.encode("utf-8")) > 4096:
        raise ActionError("coordination frame exceeds the 4 KiB native text limit")

    def invoke(client, receipt):
        if client.provenance().get("target") != payload.target:
            raise RpcError("Coordination target identity mismatch")
        _read_guarded_thread(client, sender_id, sender_cwd)
        actual = _read_guarded_thread(client, thread_id, cwd)["thread"]
        state = actual.get("status")
        flags = state.get("activeFlags") if isinstance(state, dict) else None
        safe = isinstance(state, dict) and state.get("type") == "active" and flags == []
        if not safe:
            receipt.update(
                state="rejected",
                thread_id=thread_id,
                error_code="coordination_recipient_state",
            )
            return {
                "coordination": {
                    "delivery": "not_sent",
                    "read": "unverified",
                    "reason": "recipient_state_not_safe_for_requested_mode",
                }
            }
        result = client.request(
            "turn/steer",
            {
                "threadId": thread_id,
                "expectedTurnId": payload.expected_turn_id,
                "input": [{"type": "text", "text": text}],
            },
        )
        if (
            not isinstance(result, dict)
            or result.get("turnId") != payload.expected_turn_id
        ):
            raise RpcError("Coordination turn acknowledgement identity mismatch")
        receipt.update(
            state="accepted",
            thread_id=thread_id,
            turn_id=payload.expected_turn_id,
        )
        return {
            "coordination": {
                "delivery": "native_accepted",
                "read": "unverified",
                "mode": payload.mode,
                "in_reply_to": payload.in_reply_to,
                "auto_wake": False,
            }
        }

    return _dispatch_run(
        "relay_coordination_message",
        payload,
        invoke,
        include_receipts=False,
        include_events=False,
    )
