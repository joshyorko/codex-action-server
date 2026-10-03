"""Native protocol lifecycle regressions for newly created threads."""

from __future__ import annotations

from collections import deque
import json
from pathlib import Path
import sys
import unittest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import codex_rpc  # noqa: E402


class NativeProtocolFixture:
    """Small in-memory WebSocket-shaped fixture for Codex 0.153.4 behavior.

    A fresh ``thread/start`` is attached to its creating connection but has no
    rollout. Resuming it therefore returns the observed native error. An
    existing thread has a materialized rollout and accepts ``thread/resume``.
    """

    def __init__(self, *, ephemeral: bool = False, existing: bool = False):
        self.calls = []
        self.responses = deque()
        self.closed = False
        self.ephemeral = ephemeral
        self.existing = existing
        self.thread_id = (
            "existing"
            if existing
            else ("fresh-ephemeral" if ephemeral else "fresh-durable")
        )
        self.turn_id = "existing-turn" if existing else f"turn-{self.thread_id}"
        self.resumed = False
        self.turn = None

    def send(self, raw):
        message = json.loads(raw)
        self.calls.append(message)
        method = message.get("method")
        if method == "initialized":
            return
        if method == "initialize":
            self._result(
                message,
                {"codexHome": "/codex", "userAgent": "codex-0.153.4"},
            )
        elif method == "thread/start" and not self.existing:
            self._result(
                message,
                {
                    "thread": {
                        "id": self.thread_id,
                        "cwd": "/repo",
                        "ephemeral": self.ephemeral,
                    }
                },
            )
        elif method == "thread/list":
            self._result(
                message,
                {
                    "data": [{"id": self.thread_id, "cwd": "/repo"}],
                    "nextCursor": None,
                },
            )
        elif method == "thread/read":
            self._result(
                message,
                {
                    "thread": {
                        "id": self.thread_id,
                        "cwd": "/repo",
                        "ephemeral": self.ephemeral,
                        "turns": [] if self.turn is None else [self.turn],
                    }
                },
            )
        elif method == "thread/resume":
            if not self.existing:
                self._error(message, "no rollout found for thread id " + self.thread_id)
            else:
                self.resumed = True
                self._result(
                    message,
                    {"thread": {"id": "existing", "cwd": "/repo"}},
                )
        elif method == "turn/start":
            if self.turn is not None:
                self._error(message, "duplicate turn/start")
                return
            if not self.existing and not any(
                call.get("method") == "thread/start" for call in self.calls
            ):
                self._error(message, "thread was not created on this connection")
                return
            if self.existing and not self.resumed:
                self._error(message, "thread was not resumed on this connection")
                return
            self.turn = {"id": self.turn_id, "status": "completed", "items": []}
            self._result(
                message,
                {"turn": {"id": self.turn_id, "status": "inProgress", "items": []}},
            )
            self.responses.append(
                json.dumps(
                    {
                        "method": "turn/completed",
                        "params": {"threadId": self.thread_id, "turn": self.turn},
                    }
                )
            )
        elif method in {
            "thread/turns/list",
            "thread/items/list",
            "thread/settings/update",
            "turn/settings/update",
            "thread/goal/get",
            "thread/goal/set",
            "thread/goal/clear",
            "model/list",
            "modelProvider/capabilities/read",
            "server/diagnostics",
            "mcpServerStatus/list",
            "thread/loaded/list",
        }:
            self._result(message, {})
        else:
            raise AssertionError(message)

    def recv(self, timeout=None):
        if not self.responses:
            raise AssertionError("fixture has no response")
        return self.responses.popleft()

    def close(self):
        self.closed = True

    def _result(self, message, result):
        self.responses.append(json.dumps({"id": message["id"], "result": result}))

    def _error(self, message, detail):
        self.responses.append(
            json.dumps(
                {
                    "id": message["id"],
                    "error": {"code": -32000, "message": detail},
                }
            )
        )


def connected_client(fixture):
    client = codex_rpc.Client(codex_rpc.Target("local", socket_path="fixture"))
    client.ws = fixture
    client.metadata = client.request(
        "initialize",
        {
            "clientInfo": {"name": "lifecycle-test", "version": "0"},
            "capabilities": {"experimentalApi": True},
        },
    )
    fixture.send(json.dumps({"method": "initialized"}))
    return client


class NativeLifecycleTests(unittest.TestCase):
    def test_created_thread_stays_on_connection_until_first_turn_materializes_rollout(
        self,
    ):
        for ephemeral in (False, True):
            with self.subTest(ephemeral=ephemeral):
                fixture = NativeProtocolFixture(ephemeral=ephemeral)
                client = connected_client(fixture)
                try:
                    created = client.request(
                        "thread/start",
                        {"cwd": "/repo", "ephemeral": ephemeral},
                    )
                    thread_id = created["thread"]["id"]
                    client.set_workstream("/repo", thread_id)
                    client.request(
                        "thread/read",
                        {"threadId": thread_id, "includeTurns": False},
                    )
                    attached = client.ensure_thread_attached(
                        thread_id,
                        {"threadId": thread_id, "excludeTurns": True},
                    )
                    self.assertEqual(attached["thread"]["id"], thread_id)
                    accepted = client.request(
                        "turn/start",
                        {
                            "threadId": thread_id,
                            "input": [{"type": "text", "text": "hello"}],
                        },
                    )
                    completed = client.wait_turn(
                        thread_id, accepted["turn"]["id"], timeout=1
                    )
                    read = client.request(
                        "thread/read",
                        {"threadId": thread_id, "includeTurns": True},
                    )
                finally:
                    client.close()

                self.assertEqual(completed["status"], "completed")
                self.assertEqual(read["thread"]["turns"][0]["id"], fixture.turn_id)
                methods = [message.get("method") for message in fixture.calls]
                self.assertEqual(methods.count("thread/resume"), 0)
                self.assertEqual(methods.count("turn/start"), 1)
                self.assertEqual(
                    methods,
                    [
                        "initialize",
                        "initialized",
                        "thread/start",
                        "thread/read",
                        "turn/start",
                        "thread/read",
                    ],
                )

    def test_existing_thread_still_uses_native_resume_once(self):
        fixture = NativeProtocolFixture(existing=True)
        client = connected_client(fixture)
        try:
            client.set_workstream("/repo", "existing")
            client.request(
                "thread/read",
                {"threadId": "existing", "includeTurns": False},
            )
            client.ensure_thread_attached(
                "existing",
                {"threadId": "existing", "excludeTurns": True},
            )
            accepted = client.request(
                "turn/start",
                {
                    "threadId": "existing",
                    "input": [{"type": "text", "text": "hello"}],
                },
            )
            self.assertEqual(
                client.wait_turn("existing", accepted["turn"]["id"], timeout=1)[
                    "status"
                ],
                "completed",
            )
        finally:
            client.close()

        methods = [message.get("method") for message in fixture.calls]
        self.assertEqual(methods.count("thread/resume"), 1)
        self.assertEqual(methods.count("turn/start"), 1)

    def test_priority_native_methods_are_bounded_and_workstream_scoped(self):
        fixture = NativeProtocolFixture(existing=True)
        client = connected_client(fixture)
        try:
            client.set_workstream("/repo", "existing", "existing-turn")
            calls = {
                "thread/list": {"cwd": "/repo", "limit": 1},
                "thread/turns/list": {"threadId": "existing", "limit": 1},
                "thread/items/list": {
                    "threadId": "existing",
                    "turnId": "existing-turn",
                },
                "thread/settings/update": {
                    "threadId": "existing",
                    "model": "gpt-6-luna",
                },
                "turn/settings/update": {
                    "threadId": "existing",
                    "turnId": "existing-turn",
                    "effort": "max",
                },
                "thread/goal/get": {"threadId": "existing"},
                "thread/goal/set": {"threadId": "existing", "objective": "ship"},
                "thread/goal/clear": {"threadId": "existing"},
                "model/list": {},
                "modelProvider/capabilities/read": {},
                "server/diagnostics": {},
                "mcpServerStatus/list": {},
                "thread/loaded/list": {},
            }
            for method, params in calls.items():
                with self.subTest(method=method):
                    client.request(method, params)

            sent_count = len(fixture.calls)
            with self.assertRaisesRegex(codex_rpc.RpcError, "thread identity"):
                client.request("thread/items/list", {"threadId": "other"})
            with self.assertRaisesRegex(codex_rpc.RpcError, "turn identity"):
                client.request(
                    "turn/settings/update",
                    {"threadId": "existing", "turnId": "other", "effort": "max"},
                )
            with self.assertRaisesRegex(codex_rpc.RpcError, "cwd"):
                client.request(
                    "thread/settings/update",
                    {"threadId": "existing", "cwd": "/other"},
                )
            with self.assertRaisesRegex(codex_rpc.RpcError, "cwd"):
                client.request("thread/list", {"cwd": "/other"})
            with self.assertRaisesRegex(codex_rpc.RpcError, "thread identity"):
                client.request("mcpServerStatus/list", {"threadId": "other"})
            self.assertEqual(len(fixture.calls), sent_count)
            with self.assertRaisesRegex(ValueError, "outside"):
                client.request("command/exec", {})
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
