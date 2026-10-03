from unittest.mock import patch
from test_actions import FakeClient, load_actions


def test_wait_failure_returns_accepted_identity_without_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_ACTION_RECEIPTS", str(tmp_path))
    module = load_actions()
    client = FakeClient(None)
    client.wait_turn = lambda *a, **kw: (_ for _ in ()).throw(TimeoutError("timed out"))
    request = module.TurnStartRequest(
        target="local",
        cwd="/trusted",
        thread_id="thread-1",
        text="hi",
        request_id="once",
        wait_for_completion=True,
    )
    with patch.object(module, "Client", return_value=client):
        result = module.start_turn(request).result.result
    assert result["dispatch"]["thread_id"] == "thread-1"
    assert result["dispatch"]["turn_id"] == "turn-1"
    assert result["dispatch"]["state"] == "accepted"
    with patch.object(
        module, "Client", side_effect=AssertionError("must not reconnect")
    ):
        replay = module.start_turn(request).result.result
    assert replay["dispatch"]["replayed"]


def test_missing_ack_stays_unknown_and_same_key_never_dispatches_again(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CODEX_ACTION_RECEIPTS", str(tmp_path))
    module = load_actions()
    client = FakeClient(None)
    original = client.request

    def request(method, params):
        if method == "turn/start":
            raise TimeoutError("lost response after send")
        return original(method, params)

    client.request = request
    payload = module.TurnStartRequest(
        target="local",
        cwd="/trusted",
        thread_id="thread-1",
        text="hi",
        request_id="lost",
    )
    with patch.object(module, "Client", return_value=client):
        result = module.start_turn(payload).result.result
    assert result["dispatch"]["state"] == "unknown"
    with patch.object(
        module, "Client", side_effect=AssertionError("duplicate execution")
    ):
        assert module.start_turn(payload).result.result["dispatch"]["replayed"]


def test_post_ack_guard_error_preserves_receipt(monkeypatch):
    from test_control_actions import ControlClient

    module = load_actions()
    client = ControlClient(None)
    original = client.request

    def request(method, params):
        if method == "thread/read":
            return {"thread": {"id": "different", "cwd": "/wrong"}}
        return original(method, params)

    client.request = request
    payload = module.CreateThreadAndStartTurnRequest(
        target="local", cwd="/trusted", text="test", wait_for_completion=True
    )
    with patch.object(module, "Client", return_value=client):
        result = module.create_thread_and_start_turn(payload).result.result
    assert result["dispatch"]["thread_id"]
    assert result["dispatch"]["turn_id"]
    assert result["dispatch"]["error_code"]
