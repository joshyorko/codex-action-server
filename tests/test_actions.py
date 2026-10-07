"""Typed Action Server entrypoint tests with a transport double."""

from __future__ import annotations

import importlib
from functools import wraps
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from native_wire_contracts import assert_native_request_contract

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))


class ActionTestNamespace:
    """Exercise public wrappers while patching their actual implementation owners.

    This adapter exists only in tests. Production modules retain ordinary Python
    globals and imports, with no forwarding hooks or module replacement.
    """

    def __init__(self, public, shared):
        object.__setattr__(self, "_modules", [*shared, public])
        for module in self._modules:
            for name, value in vars(module).items():
                if not name.startswith("__"):
                    object.__setattr__(self, name, value)
        object.__setattr__(self, "public", public)

    def __setattr__(self, name, value):
        for module in self._modules:
            if name in vars(module):
                setattr(module, name, value)
        object.__setattr__(self, name, value)


def load_actions():
    fake_actions = types.ModuleType("actions")

    class FakeResponse:
        def __init__(self, result):
            self.result = result

        def model_dump(self, mode="python"):
            result = (
                self.result.model_dump(mode=mode)
                if hasattr(self.result, "model_dump")
                else self.result
            )
            return {"result": result}

    def action(**_kwargs):
        return lambda function: function

    fake_actions.ActionError = RuntimeError
    fake_actions.Response = FakeResponse
    fake_actions.action = action
    with patch.dict(sys.modules, {"actions": fake_actions}):
        # Reload every module capturing framework classes. Otherwise a preceding
        # real-runtime import can leak a real Response into transport-double tests.
        for name in tuple(sys.modules):
            if name in {
                "codex_actions",
                "capability_registration",
                "codex_shared",
            } or name.startswith("codex_shared."):
                sys.modules.pop(name)
        public = importlib.import_module("codex_actions")
        shared = [
            module
            for name, module in sys.modules.items()
            if name.startswith("codex_shared.")
        ]
        loaded = {
            name: module
            for name, module in sys.modules.items()
            if name in {"codex_actions", "capability_registration", "codex_shared"}
            or name.startswith("codex_shared.")
        }
    # patch.dict restores pre-existing cached modules on exit. Retain the source
    # modules just loaded so imports and wrapper globals have the same owners.
    sys.modules.update(loaded)
    return ActionTestNamespace(public, shared)


class FakeClient:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        request = cls.__dict__.get("request")
        if request is None:
            return

        @wraps(request)
        def validate_request(self, method, params):
            assert_native_request_contract(method, params)
            return request(self, method, params)

        cls.request = validate_request

    def __init__(self, _target):
        self.calls = []
        self.events = []
        self.receipts = []
        self.workstreams = []
        self.metadata = {"codexHome": "/codex"}
        self._provenance = {
            "target": "local",
            "codexBin": "codex",
            "socket": "/tmp/codex.sock",
            "codexHome": "/codex",
            "server": "test-server",
        }

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def provenance(self):
        return self._provenance

    def set_workstream(self, cwd, thread_id, turn_id=None):
        self.workstreams.append((cwd, thread_id, turn_id))

    def request(self, method, params):
        assert_native_request_contract(method, params)
        self.calls.append((method, params))
        if method == "thread/read":
            return {"thread": {"id": params["threadId"], "cwd": "/trusted"}}
        if method == "thread/list":
            return {"data": [{"id": "thread-1", "cwd": "/trusted"}], "nextCursor": None}
        if method == "thread/start":
            return {"thread": {"id": "thread-new", "cwd": params["cwd"]}}
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"], "cwd": "/trusted"}}
        if method == "turn/start":
            return {"turn": {"id": "turn-1", "status": "inProgress"}}
        if method == "turn/steer":
            return {"turnId": "turn-1"}
        if method == "turn/interrupt":
            return {}
        raise AssertionError(method)

    def wait_turn(self, thread_id, turn_id, timeout=300):
        self.calls.append(("wait_turn", {"threadId": thread_id, "turnId": turn_id}))
        return {"id": turn_id, "status": "completed"}


class ActionContractTests(unittest.TestCase):
    def test_discovery_is_typed_and_target_scoped(self):
        module = load_actions()
        client = FakeClient(None)
        with patch.object(module, "Client", return_value=client):
            response = module.discover_threads(
                module.ThreadListRequest(target="local", cwd="/trusted", limit=10)
            )
        self.assertEqual(response.result.result["data"][0]["id"], "thread-1")
        self.assertEqual(
            client.calls,
            [
                (
                    "thread/list",
                    {
                        "cwd": "/trusted",
                        "limit": 10,
                    },
                ),
            ],
        )

    def test_read_requires_and_verifies_workstream_cwd(self):
        module = load_actions()
        client = FakeClient(None)
        with patch.object(module, "Client", return_value=client):
            response = module.read_thread(
                module.ThreadReadRequest(
                    target="local", cwd="/trusted", thread_id="thread-1"
                )
            )
        self.assertEqual(response.result.result["thread"]["cwd"], "/trusted")
        self.assertEqual(
            client.calls,
            [
                ("thread/read", {"threadId": "thread-1", "includeTurns": False}),
            ],
        )

    def test_read_rejects_a_thread_from_another_workstream(self):
        module = load_actions()
        client = FakeClient(None)
        client.request = lambda *_args: {
            "thread": {"id": "thread-1", "cwd": "/other-worktree"}
        }
        with patch.object(module, "Client", return_value=client):
            with self.assertRaisesRegex(module.ActionError, "cwd"):
                module.read_thread(
                    module.ThreadReadRequest(
                        target="local", cwd="/trusted", thread_id="thread-1"
                    )
                )

    def test_read_rejects_a_different_returned_thread_id(self):
        module = load_actions()
        client = FakeClient(None)
        client.request = lambda *_args: {
            "thread": {"id": "thread-other", "cwd": "/trusted"}
        }
        with patch.object(module, "Client", return_value=client):
            with self.assertRaisesRegex(module.ActionError, "thread"):
                module.read_thread(
                    module.ThreadReadRequest(
                        target="local", cwd="/trusted", thread_id="thread-1"
                    )
                )

    def test_mutation_rejects_a_different_returned_thread_id(self):
        module = load_actions()
        client = FakeClient(None)
        client.request = lambda *_args: {
            "thread": {"id": "thread-other", "cwd": "/trusted"}
        }
        with patch.object(module, "Client", return_value=client):
            with self.assertRaisesRegex(module.ActionError, "thread"):
                module.interrupt_turn(
                    module.TurnInterruptRequest(
                        target="local",
                        cwd="/trusted",
                        thread_id="thread-1",
                        turn_id="turn-1",
                    )
                )

    def test_mutations_read_and_verify_cwd_before_native_request(self):
        module = load_actions()
        client = FakeClient(None)
        request = module.TurnSteerRequest(
            target="local",
            cwd="/trusted",
            thread_id="thread-1",
            turn_id="turn-1",
            text="continue",
        )
        with patch.object(module, "Client", return_value=client):
            response = module.steer_turn(request)
        self.assertEqual(response.result.result, {"turnId": "turn-1"})
        self.assertEqual(
            [method for method, _params in client.calls],
            [
                "thread/read",
                "turn/steer",
            ],
        )
        self.assertEqual(
            client.calls[-1][1],
            {
                "threadId": "thread-1",
                "expectedTurnId": "turn-1",
                "input": [{"type": "text", "text": "continue"}],
            },
        )

    def test_thread_start_and_resume_are_explicit_lifecycle_operations(self):
        module = load_actions()
        client = FakeClient(None)
        with patch.object(module, "Client", return_value=client):
            started = module.start_thread(
                module.ThreadStartRequest(target="local", cwd="/trusted")
            )
            resumed = module.resume_thread(
                module.ThreadResumeRequest(
                    target="local", cwd="/trusted", thread_id="thread-1"
                )
            )
        self.assertEqual(started.result.result["thread"]["cwd"], "/trusted")
        self.assertEqual(resumed.result.result["thread"]["id"], "thread-1")
        self.assertEqual(
            [method for method, _params in client.calls],
            [
                "thread/start",
                "thread/read",
                "thread/resume",
            ],
        )

    def test_interrupt_requires_the_expected_turn_id(self):
        module = load_actions()
        client = FakeClient(None)
        with patch.object(module, "Client", return_value=client):
            response = module.interrupt_turn(
                module.TurnInterruptRequest(
                    target="local",
                    cwd="/trusted",
                    thread_id="thread-1",
                    turn_id="turn-1",
                )
            )
        self.assertEqual(response.result.result, {})
        self.assertEqual(
            client.calls[-1],
            ("turn/interrupt", {"threadId": "thread-1", "turnId": "turn-1"}),
        )

    def test_start_turn_resumes_same_connection_and_can_wait(self):
        module = load_actions()
        client = FakeClient(None)
        request = module.TurnStartRequest(
            target="local",
            cwd="/trusted",
            thread_id="thread-1",
            text="run proof",
            wait_for_completion=True,
        )
        with patch.object(module, "Client", return_value=client):
            response = module.start_turn(request)
        self.assertEqual(response.result.result["completed"]["status"], "completed")
        self.assertEqual(
            [method for method, _params in client.calls],
            [
                "thread/read",
                "thread/resume",
                "turn/start",
                "wait_turn",
            ],
        )
        self.assertEqual(client.workstreams, [("/trusted", "thread-1", None)])

    def test_approval_or_rpc_failure_is_returned_closed(self):
        module = load_actions()
        client = FakeClient(None)
        client.request = lambda *_args: (_ for _ in ()).throw(
            module.RpcError("Server requires approval/input")
        )
        with patch.object(module, "Client", return_value=client):
            with self.assertRaisesRegex(
                module.ActionError, "Server requires approval/input"
            ):
                module.read_thread(
                    module.ThreadReadRequest(
                        target="local", cwd="/trusted", thread_id="thread-1"
                    )
                )

    def test_entrypoints_do_not_accept_raw_method_or_shell_arguments(self):
        import inspect

        module = load_actions()
        for name in (
            "discover_threads",
            "read_thread",
            "start_thread",
            "resume_thread",
            "start_turn",
            "steer_turn",
            "interrupt_turn",
        ):
            with self.subTest(name=name):
                names = set(inspect.signature(getattr(module, name)).parameters)
                self.assertNotIn("method", names)
                self.assertNotIn("command", names)
                self.assertNotIn("shell", names)

    def test_request_models_forbid_raw_rpc_fields(self):
        module = load_actions()
        with self.assertRaises(Exception):
            module.ThreadReadRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                method="turn/start",
                params={"command": "id"},
            )

    def test_invalid_action_identity_fails_before_client_creation(self):
        module = load_actions()
        with patch.object(
            module, "Client", side_effect=AssertionError("connected too early")
        ):
            with self.assertRaisesRegex(module.ActionError, "absolute"):
                module.start_turn(
                    module.TurnStartRequest(
                        target="local", cwd="relative", thread_id="thread-1", text="run"
                    )
                )


if __name__ == "__main__":
    unittest.main()
