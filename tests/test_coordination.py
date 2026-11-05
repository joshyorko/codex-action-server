"""Operator relay validates both identities and never wakes a native thread."""

import json
from unittest.mock import patch

import pytest
from websockets.exceptions import InvalidHandshake, InvalidMessage

from test_actions import FakeClient, load_actions


SENDER = {"target": "local", "cwd": "/trusted", "thread_id": "sender"}
RECIPIENT = {"target": "local", "cwd": "/trusted", "thread_id": "recipient"}


class RelayClient(FakeClient):
    def __init__(self, *, status="active", flags=None, response=None):
        super().__init__(None)
        self.status = status
        self.flags = flags or []
        self.response = response

    def request(self, method, params):
        self.calls.append((method, params))
        if method == "thread/read":
            return {
                "thread": {
                    "id": params["threadId"],
                    "cwd": "/trusted",
                    "status": {"type": self.status, "activeFlags": self.flags},
                }
            }
        if method == "turn/steer":
            if isinstance(self.response, Exception):
                raise self.response
            return self.response if self.response is not None else {"turnId": "turn-1"}
        raise AssertionError(method)


@pytest.fixture
def relay(tmp_path, monkeypatch):
    policy = tmp_path / "coordination.json"
    policy.write_text(
        json.dumps(
            {
                "pairs": [
                    {"sender": SENDER, "recipient": RECIPIENT},
                    {"sender": RECIPIENT, "recipient": SENDER},
                ]
            }
        )
    )
    policy.chmod(0o600)
    monkeypatch.setenv("CODEX_ACTION_COORDINATION_POLICY", str(policy))
    monkeypatch.setenv("CODEX_ACTION_RECEIPTS", str(tmp_path / "receipts"))
    module = load_actions()
    return module, policy, tmp_path


def request(module, **values):
    return module.CoordinationRelayRequest(
        **{
            **RECIPIENT,
            "sender": SENDER,
            "request_id": "message-1",
            "mode": "steer",
            "expected_turn_id": "turn-1",
            "text": "bounded hello",
            **values,
        }
    )


def invoke(module, client, payload):
    with patch.object(module, "Client", return_value=client):
        return module.relay_coordination_message(payload).result


def test_opt_in_message_and_ack_use_no_turn_start_and_receipts_survive_reconnect(relay):
    module, _, root = relay
    client = RelayClient()
    payload = request(module)
    sent = invoke(module, client, payload)
    assert sent.result["dispatch"]["state"] == "accepted"
    assert sent.result["coordination"]["delivery"] == "native_accepted"
    assert sent.result["coordination"]["read"] == "unverified"
    assert sent.receipts == [] and sent.events == []
    wire = client.calls[-1]
    assert wire[0] == "turn/steer"
    text = wire[1]["input"][0]["text"]
    assert "operator-relayed" in text and "sender" in text and "bounded hello" in text
    assert wire[1]["expectedTurnId"] == "turn-1"
    with patch.object(
        module, "Client", side_effect=AssertionError("must not reconnect")
    ):
        replay = module.relay_coordination_message(payload).result.result
    assert replay["dispatch"]["replayed"] is True
    ack_client = RelayClient()
    ack = invoke(
        module,
        ack_client,
        module.CoordinationRelayRequest(
            **SENDER,
            sender=RECIPIENT,
            request_id="ack-1",
            mode="steer",
            expected_turn_id="turn-1",
            text="received message-1",
            in_reply_to="message-1",
        ),
    )
    assert ack.result["dispatch"]["state"] == "accepted"
    assert all(
        method
        not in {
            "turn/start",
            "thread/resume",
            "turn/interrupt",
            "thread/inject_items",
            "thread/queue/add",
        }
        for method, _ in client.calls + ack_client.calls
    )
    saved = (root / "receipts/message-1.json").read_text()
    assert "bounded hello" not in saved


@pytest.mark.parametrize(
    "change",
    [
        {"sender": {**SENDER, "target": "other"}},
        {"sender": {**SENDER, "cwd": "/unknown"}},
        {"thread_id": "unknown"},
    ],
)
def test_unapproved_identity_pair_fails_before_connection(relay, change):
    module, _, _ = relay
    with patch.object(module, "Client", side_effect=AssertionError("not authorized")):
        with pytest.raises((module.ActionError, ValueError)):
            module.relay_coordination_message(request(module, **change))


@pytest.mark.parametrize("state", ["idle", "notLoaded", "systemError", "future"])
def test_steer_refuses_idle_unloaded_and_unknown_recipients(relay, state):
    module, _, _ = relay
    client = RelayClient(status=state)
    result = invoke(module, client, request(module)).result
    assert result["dispatch"]["state"] == "rejected"
    assert all(method == "thread/read" for method, _ in client.calls)


def test_active_steer_binds_expected_turn_and_does_not_claim_read(relay):
    module, _, _ = relay
    client = RelayClient(status="active")
    result = invoke(
        module, client, request(module, mode="steer", expected_turn_id="turn-1")
    ).result
    assert result["dispatch"]["state"] == "accepted"
    assert client.calls[-1][1]["expectedTurnId"] == "turn-1"
    assert result["coordination"]["read"] == "unverified"


def test_waiting_active_recipient_is_not_steered(relay):
    module, _, _ = relay
    client = RelayClient(status="active", flags=["waitingOnApproval"])
    result = invoke(
        module, client, request(module, mode="steer", expected_turn_id="turn-1")
    ).result
    assert result["dispatch"]["state"] == "rejected"
    assert all(method == "thread/read" for method, _ in client.calls)


@pytest.mark.parametrize("error_type", [TimeoutError, InvalidHandshake, InvalidMessage])
def test_unknown_ack_is_reserved_and_never_dispatched_twice(relay, error_type):
    module, _, _ = relay
    client = RelayClient(response=error_type("private transport data"))
    payload = request(module)
    result = invoke(module, client, payload).result
    assert result["dispatch"]["state"] == "unknown"
    assert "private" not in json.dumps(result)
    second = invoke(module, client, payload).result
    assert second["dispatch"]["replayed"] is True
    assert sum(method == "turn/steer" for method, _ in client.calls) == 1


def test_native_identity_mismatch_prevents_mutation(relay):
    module, _, _ = relay
    client = RelayClient()
    client.request = lambda *_: {"thread": {"id": "other", "cwd": "/trusted"}}
    result = invoke(module, client, request(module)).result
    assert result["dispatch"]["state"] != "accepted"


@pytest.mark.parametrize(
    "values",
    [
        {"text": "🙂" * 513},
        {"text": " "},
        {"mode": "inject"},
        {"mode": "queue"},
        {"expected_turn_id": None},
        {"request_id": None},
        {"sender": RECIPIENT},
        {"method": "turn/start"},
    ],
)
def test_invalid_relay_requests_are_rejected(relay, values):
    module, _, _ = relay
    with pytest.raises(ValueError):
        request(module, **values)


@pytest.mark.parametrize("policy_kind", ["missing", "public", "symlink", "duplicate"])
def test_policy_is_opt_in_private_and_unambiguous(relay, monkeypatch, policy_kind):
    module, policy, _ = relay
    if policy_kind == "missing":
        monkeypatch.delenv("CODEX_ACTION_COORDINATION_POLICY")
    elif policy_kind == "public":
        policy.chmod(0o644)
    elif policy_kind == "duplicate":
        policy.write_text('{"pairs":[],"pairs":[]}')
    else:
        link = policy.with_name("link.json")
        link.symlink_to(policy)
        monkeypatch.setenv("CODEX_ACTION_COORDINATION_POLICY", str(link))
    with patch.object(module, "Client", side_effect=AssertionError("not authorized")):
        with pytest.raises(module.ActionError):
            module.relay_coordination_message(request(module))


def test_capability_reports_cas_board_boundary_without_global_feature_claims():
    from native_capabilities import inventory

    report = inventory("0.166.0")["coordination"]
    assert report["status"] == "native_board_unavailable_for_independent_roots"
    assert report["native_board_rpc_exposed"] is False
    assert report["model_tools"] == "unverified"
    assert report["auto_wake"] is False


@pytest.mark.parametrize("profile,exposed", [("observe", False), ("operator", True)])
def test_capability_reports_effective_relay_exposure(profile, exposed, monkeypatch):
    import action_catalog_contract
    from native_capabilities import inventory

    monkeypatch.setattr(action_catalog_contract, "IMPLEMENTED_PROFILE", profile)
    report = inventory("0.166.0")["coordination"]
    assert report["relay_exposed"] is exposed


@pytest.mark.parametrize("response", [{"turnId": "other"}, {}, None])
def test_wrong_native_turn_acknowledgement_never_claims_acceptance(relay, response):
    module, _, _ = relay
    client = RelayClient(response=response)
    if response is None:
        client.response = {"turnId": None}
    result = invoke(module, client, request(module)).result
    assert result["dispatch"]["state"] == "unknown"


def test_target_provenance_mismatch_fails_before_native_reads(relay):
    module, _, _ = relay
    client = RelayClient()
    client._provenance["target"] = "other"
    result = invoke(module, client, request(module)).result
    assert result["dispatch"]["state"] != "accepted"
    assert client.calls == []


def test_native_wire_message_reply_and_reconnect_have_exactly_two_effects(
    relay, tmp_path
):
    import threading

    from websockets.sync.server import unix_serve
    from native_test_helpers import native_server_user_agent
    import codex_rpc

    module, _, _ = relay
    effects = []
    calls = []

    def handle(websocket):
        for raw in websocket:
            message = json.loads(raw)
            method = message["method"]
            calls.append(method)
            if method == "initialized":
                continue
            if method == "initialize":
                result = {
                    "userAgent": native_server_user_agent("0.166.0"),
                    "codexHome": "/isolated-fixture",
                }
            elif method == "thread/read":
                result = {
                    "thread": {
                        "id": message["params"]["threadId"],
                        "cwd": "/trusted",
                        "status": {"type": "active", "activeFlags": []},
                    }
                }
            elif method == "turn/steer":
                effects.append(message["params"])
                result = {"turnId": message["params"]["expectedTurnId"]}
            else:
                raise AssertionError(method)
            websocket.send(json.dumps({"id": message["id"], "result": result}))

    socket = tmp_path / "relay.sock"
    target = codex_rpc.Target("local", socket_path=str(socket))
    with unix_serve(handle, str(socket)) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.object(module, "resolve_target", return_value=target):
                sent = module.relay_coordination_message(request(module)).result.result
                ack_payload = module.CoordinationRelayRequest(
                    **SENDER,
                    sender=RECIPIENT,
                    request_id="ack-wire",
                    mode="steer",
                    expected_turn_id="turn-2",
                    text="received message-1",
                    in_reply_to="message-1",
                )
                ack = module.relay_coordination_message(ack_payload).result.result
                replay = module.relay_coordination_message(
                    request(module)
                ).result.result
        finally:
            server.shutdown()
            worker.join(timeout=5)
    assert sent["dispatch"]["state"] == ack["dispatch"]["state"] == "accepted"
    assert replay["dispatch"]["replayed"] is True
    assert [(effect["threadId"], effect["expectedTurnId"]) for effect in effects] == [
        ("recipient", "turn-1"),
        ("sender", "turn-2"),
    ]
    assert set(calls) == {"initialize", "initialized", "thread/read", "turn/steer"}
