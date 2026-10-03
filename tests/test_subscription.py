from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import threading
import unittest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
spec = importlib.util.spec_from_file_location("codex_rpc", SRC / "codex_rpc.py")
codex_rpc = importlib.util.module_from_spec(spec)
sys.modules["codex_rpc"] = codex_rpc
spec.loader.exec_module(codex_rpc)


class FakeClient(codex_rpc.Client):
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.events = []
        self.receipts = []
        self.target = codex_rpc.Target("local")
        self.bridge = None

    def request(self, method, params, timeout=None):
        self.calls.append((method, params))
        return next(self.responses)


class QuietClient(FakeClient):
    def __init__(self, responses, receive_error=TimeoutError("idle")):
        super().__init__(responses)
        self.receive_error = receive_error

    def receive(self, _timeout):
        if isinstance(self.receive_error, BaseException):
            raise self.receive_error
        error = (
            self.receive_error.pop(0) if self.receive_error else TimeoutError("idle")
        )
        if error is not None:
            raise error


def live_quiet_client():
    from websockets.sync.server import serve
    from websockets.sync.client import connect

    def handler(ws):
        for raw in ws:
            message = __import__("json").loads(raw)
            if message["method"] == "thread/resume":
                ws.send(
                    __import__("json").dumps(
                        {
                            "id": message["id"],
                            "result": {"thread": {"id": "thread-1", "cwd": "/work"}},
                        }
                    )
                )
            elif message["method"] == "thread/read":
                ws.send(
                    __import__("json").dumps(
                        {
                            "id": message["id"],
                            "result": {
                                "thread": {
                                    "id": "thread-1",
                                    "cwd": "/work",
                                    "turns": [{"id": "turn-1", "status": "inProgress"}],
                                }
                            },
                        }
                    )
                )

    server = serve(handler, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.socket.getsockname()[1]
    client = codex_rpc.Client(codex_rpc.Target("local"))
    client.ws = connect(f"ws://127.0.0.1:{port}")
    return client, server


class ExistingTurnSubscriptionTests(unittest.TestCase):
    def test_quiet_alive_turn_returns_timed_out_receipt_at_deadline(self):
        client = QuietClient(
            [
                {"thread": {"id": "thread-1", "cwd": "/work"}},
                {
                    "thread": {
                        "id": "thread-1",
                        "cwd": "/work",
                        "turns": [{"id": "turn-1", "status": "inProgress"}],
                    }
                },
                {
                    "thread": {
                        "id": "thread-1",
                        "cwd": "/work",
                        "turns": [{"id": "turn-1", "status": "inProgress"}],
                    }
                },
            ]
        )
        result = client.subscribe_turn("/work", "thread-1", "turn-1", deadline=0.01)
        self.assertEqual(result["status"], "timed_out")
        self.assertEqual(client.receipts[-1]["status"], "timed_out")

    def test_intermittent_receive_timeout_continues_until_deadline(self):
        client = QuietClient(
            [
                {"thread": {"id": "thread-1", "cwd": "/work"}},
                {
                    "thread": {
                        "id": "thread-1",
                        "cwd": "/work",
                        "turns": [{"id": "turn-1", "status": "inProgress"}],
                    }
                },
                {
                    "thread": {
                        "id": "thread-1",
                        "cwd": "/work",
                        "turns": [{"id": "turn-1", "status": "inProgress"}],
                    }
                },
            ],
            receive_error=[
                TimeoutError("idle"),
                TimeoutError("idle"),
                TimeoutError("idle"),
            ],
        )
        result = client.subscribe_turn("/work", "thread-1", "turn-1", deadline=0.003)
        self.assertEqual(result["status"], "timed_out")

    def test_final_state_reconciliation_wins_race_at_deadline(self):
        client = QuietClient(
            [
                {"thread": {"id": "thread-1", "cwd": "/work"}},
                {
                    "thread": {
                        "id": "thread-1",
                        "cwd": "/work",
                        "turns": [{"id": "turn-1", "status": "inProgress"}],
                    }
                },
                {
                    "thread": {
                        "id": "thread-1",
                        "cwd": "/work",
                        "turns": [{"id": "turn-1", "status": "completed"}],
                    }
                },
            ]
        )
        result = client.subscribe_turn("/work", "thread-1", "turn-1", deadline=0.01)
        self.assertEqual(result["status"], "completed")

    def test_real_websocket_idle_timeout_returns_receipt(self):
        client, server = live_quiet_client()
        try:
            result = client.subscribe_turn("/work", "thread-1", "turn-1", deadline=0.01)
            self.assertEqual(result["status"], "timed_out")
            self.assertEqual(client.receipts[-1]["status"], "timed_out")
        finally:
            client.close()
            server.shutdown()

    def test_resume_precedes_read_and_consumes_completion_buffered_by_read(self):
        client = FakeClient(
            [
                {"thread": {"id": "thread-1", "cwd": "/work"}},
                {
                    "thread": {
                        "id": "thread-1",
                        "cwd": "/work",
                        "turns": [{"id": "turn-1", "status": "completed"}],
                    }
                },
            ]
        )
        result = client.subscribe_turn("/work", "thread-1", "turn-1", deadline=1)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            [method for method, _ in client.calls], ["thread/resume", "thread/read"]
        )
        self.assertEqual(result["thread_id"], "thread-1")

    def test_wrong_turn_does_not_report_success(self):
        client = FakeClient(
            [
                {"thread": {"id": "thread-1", "cwd": "/work"}},
                {
                    "thread": {
                        "id": "thread-1",
                        "cwd": "/work",
                        "turns": [{"id": "other", "status": "completed"}],
                    }
                },
            ]
        )
        with self.assertRaises(codex_rpc.RpcError):
            client.receive = lambda _timeout: (_ for _ in ()).throw(
                codex_rpc.RpcError("missing turn")
            )
            client.subscribe_turn("/work", "thread-1", "turn-1", deadline=0.01)
        self.assertNotIn("turn/start", [method for method, _ in client.calls])

    def test_terminal_statuses_are_explicit(self):
        for status in ("completed", "interrupted", "failed"):
            client = FakeClient(
                [
                    {"thread": {"id": "thread-1", "cwd": "/work"}},
                    {
                        "thread": {
                            "id": "thread-1",
                            "cwd": "/work",
                            "turns": [{"id": "turn-1", "status": status}],
                        }
                    },
                ]
            )
            result = client.subscribe_turn("/work", "thread-1", "turn-1", deadline=1)
            self.assertEqual(result["status"], status)

    def test_disconnect_is_a_receipt_not_success(self):
        client = FakeClient(
            [
                {"thread": {"id": "thread-1", "cwd": "/work"}},
            ]
        )
        client.request = (
            lambda method, params, timeout=None: (_ for _ in ()).throw(OSError("gone"))
            if method == "thread/read"
            else {"thread": {"id": "thread-1", "cwd": "/work"}}
        )
        with self.assertRaises(OSError):
            client.subscribe_turn("/work", "thread-1", "turn-1", deadline=1)
        self.assertEqual(client.receipts[-1]["status"], "disconnect")

    def test_identity_mismatch_is_not_converted_to_timeout(self):
        client = FakeClient(
            [
                {"thread": {"id": "thread-1", "cwd": "/work"}},
                {"thread": {"id": "other", "cwd": "/work"}},
            ]
        )
        with self.assertRaises(codex_rpc.RpcError):
            client.subscribe_turn("/work", "thread-1", "turn-1", deadline=0.01)
        self.assertNotEqual(client.receipts[-1].get("status"), "timed_out")


if __name__ == "__main__":
    unittest.main()
