"""Real Actions Runtime and loopback MCP validation.

The MCP case starts the installed Action Server CLI, but substitutes a
temporary Unix WebSocket for the native daemon.  It therefore proves the
package/runtime boundary without contacting a configured Codex target. It
enables action synchronization from ``--dir`` so no stale or absent import
catalog is used.
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


PACKAGE = Path(__file__).resolve().parents[1]
SRC = PACKAGE / "src"
ACTION_SERVER_ENV = "FRIDAY_ACTIONS_PYTHON"


def _runtime_import_code() -> str:
    return (
        "import actions; "
        "assert actions.__version__ == '1.0.1'; "
        "import codex_actions; "
        "assert callable(codex_actions.start_thread); "
        "assert callable(codex_actions.start_turn); "
        "assert callable(codex_actions.create_thread_and_start_turn)"
    )


def _actions_runtime_python() -> str | None:
    candidates = []
    configured = os.environ.get(ACTION_SERVER_ENV)
    if configured:
        candidates.append(configured)
    candidates.append(sys.executable)
    for candidate in dict.fromkeys(candidates):
        result = subprocess.run(
            # Discover the framework only; package imports belong to the
            # assertion below and must fail, not masquerade as missing runtime.
            [candidate, "-c", "import actions; assert actions.__version__ == '1.0.1'"],
            cwd=PACKAGE,
            env={**os.environ, "PYTHONPATH": str(SRC)},
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return candidate
    return None


class NativeFixture:
    """Small native App Server seam with real WebSocket framing."""

    def __init__(self, socket_path: Path, cwd: Path, codex_home: Path):
        from websockets.sync.server import unix_serve

        self.socket_path = socket_path
        self.cwd = str(cwd)
        self.codex_home = str(codex_home)
        self.thread_id = "fixture-thread"
        self.turn_id = "fixture-turn"
        self.calls: list[dict] = []
        self._lock = threading.Lock()
        self._server = unix_serve(self._handle, str(socket_path), compression=None)
        self._server.__enter__()
        self._thread = threading.Thread(
            target=self._serve,
            name="friday-native-fixture",
            daemon=True,
        )
        self._thread.start()

    def _serve(self) -> None:
        self._server.serve_forever()

    def _thread_state(self, include_turns: bool) -> dict:
        thread = {"id": self.thread_id, "cwd": self.cwd}
        if include_turns:
            thread["turns"] = [
                {
                    "id": self.turn_id,
                    "status": "completed",
                    "items": [],
                }
            ]
        return {"thread": thread}

    def _handle(self, ws) -> None:
        while True:
            try:
                message = json.loads(ws.recv())
            except Exception:
                return
            if not isinstance(message, dict):
                return
            with self._lock:
                self.calls.append(message)
            method = message.get("method")
            request_id = message.get("id")
            if method == "initialized":
                continue
            if request_id is None:
                continue
            if method == "initialize":
                result = {
                    "userAgent": "friday-validation-native-fixture/0.153.4",
                    "codexHome": self.codex_home,
                }
            elif method == "thread/list":
                result = {
                    "data": [{"id": self.thread_id, "cwd": self.cwd}],
                    "nextCursor": None,
                }
            elif method == "thread/start":
                result = self._thread_state(False)
            elif method == "thread/read":
                result = self._thread_state(
                    message.get("params", {}).get("includeTurns", False)
                )
            elif method == "thread/resume":
                result = self._thread_state(False)
            elif method == "turn/start":
                params = message.get("params", {})
                if params.get("threadId") != self.thread_id:
                    result = {"turn": {"id": self.turn_id, "status": "failed"}}
                else:
                    result = {
                        "turn": {
                            "id": self.turn_id,
                            "status": "inProgress",
                            "model": params.get("model", "fixture-model"),
                            "effort": params.get("effort", "fixture-effort"),
                        }
                    }
            else:
                result = {}
            ws.send(json.dumps({"id": request_id, "result": result}))
            if method == "turn/start":
                ws.send(
                    json.dumps(
                        {
                            "method": "turn/completed",
                            "params": {
                                "threadId": self.thread_id,
                                "turn": {
                                    "id": self.turn_id,
                                    "status": "completed",
                                },
                            },
                        }
                    )
                )

    def close(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.__exit__(None, None, None)


def _write_codex_probe(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "if sys.argv[1:] != ['app-server', 'daemon', 'version']:\n"
        "    raise SystemExit(2)\n"
        "print(json.dumps({'status': 'running', 'socketPath': "
        "os.environ['FRIDAY_VALIDATION_NATIVE_SOCKET']}))\n"
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _result_data(result):
    if getattr(result, "is_error", getattr(result, "isError", False)):
        detail = " ".join(
            getattr(item, "text", "") for item in getattr(result, "content", [])
        )
        raise AssertionError(f"typed MCP call returned an error: {detail}")
    structured = getattr(
        result,
        "structured_content",
        getattr(result, "structuredContent", None),
    )
    if not isinstance(structured, dict):
        raise AssertionError("typed MCP call did not return structured_content")
    if "operation" in structured:
        return structured
    nested = structured.get("result")
    if isinstance(nested, dict) and "operation" in nested:
        return nested
    raise AssertionError(f"unexpected Action Server structured content: {structured!r}")


class ActionServerValidationTests(unittest.TestCase):
    def test_package_import_regression_fails_instead_of_skipping(self):
        if _actions_runtime_python() is None:
            self.skipTest("Actions Runtime interpreter unavailable")
        with patch(
            __name__ + "._runtime_import_code",
            return_value=(
                "import codex_actions; raise ImportError('package import regression')"
            ),
        ):
            try:
                with self.assertRaisesRegex(
                    AssertionError, "package import regression"
                ):
                    self.test_actual_actions_runtime_subprocess_imports_package()
            except unittest.SkipTest as error:
                self.fail(f"Package import failure was hidden as a skip: {error}")

    def test_actual_actions_runtime_subprocess_imports_package(self):
        runtime = _actions_runtime_python()
        if runtime is None:
            self.skipTest(
                "Actions Runtime 1.0.1 interpreter unavailable; set "
                f"{ACTION_SERVER_ENV} or run the package dev task"
            )
        result = subprocess.run(
            [runtime, "-c", _runtime_import_code()],
            cwd=PACKAGE,
            env={**os.environ, "PYTHONPATH": str(SRC)},
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_loopback_mcp_uses_typed_actions_and_isolated_native_fixture(self):
        try:
            import anyio
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as error:
            self.skipTest(f"MCP validation dependencies unavailable: {error}")

        action_server = shutil.which("action-server")
        if action_server is None:
            self.skipTest("installed Action Server CLI is unavailable")

        with tempfile.TemporaryDirectory(
            prefix="friday-codex-action-validation-"
        ) as raw:
            root = Path(raw)
            worktree = root / "worktree"
            codex_home = root / "codex-home"
            worktree.mkdir()
            codex_home.mkdir()
            native_socket = root / "native.sock"
            codex_probe = root / "codex"
            _write_codex_probe(codex_probe)
            try:
                native = NativeFixture(native_socket, worktree, codex_home)
            except (ImportError, OSError, PermissionError) as error:
                self.skipTest(f"isolated native Unix socket unavailable: {error}")
            server_log = root / "action-server.log"
            port = _free_port()
            environment = {
                **os.environ,
                "PATH": f"{root}{os.pathsep}{os.environ.get('PATH', '')}",
                "FRIDAY_VALIDATION_NATIVE_SOCKET": str(native_socket),
            }
            server_output = server_log.open("w")
            try:
                process = subprocess.Popen(
                    [
                        action_server,
                        "start",
                        "--dir",
                        str(PACKAGE),
                        "--datadir",
                        str(root / "action-server-data"),
                        "--address",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--actions-sync=true",
                        "--min-processes",
                        "1",
                        "--max-processes",
                        "1",
                        "--parent-pid",
                        str(os.getpid()),
                    ],
                    cwd=PACKAGE,
                    env=environment,
                    stdout=server_output,
                    stderr=subprocess.STDOUT,
                )
            finally:
                server_output.close()
            try:
                self._wait_for_loopback(process, port, server_log)

                async def exercise() -> None:
                    async with streamable_http_client(
                        f"http://127.0.0.1:{port}/mcp"
                    ) as streams:
                        read_stream, write_stream = streams[:2]
                        async with ClientSession(
                            read_stream,
                            write_stream,
                            read_timeout_seconds=timedelta(seconds=10),
                        ) as session:
                            initialized = await session.initialize()
                            server_info = getattr(
                                initialized,
                                "server_info",
                                getattr(initialized, "serverInfo", None),
                            )
                            self.assertTrue(server_info.name)
                            listed = await session.list_tools()
                            tools = {tool.name: tool for tool in listed.tools}
                            required = {
                                "discover_threads",
                                "list_thread_turns",
                                "list_thread_items",
                                "read_thread",
                                "update_thread_settings",
                                "update_turn_settings",
                                "get_thread_goal",
                                "set_thread_goal",
                                "clear_thread_goal",
                                "list_models",
                                "read_model_provider_capabilities",
                                "read_server_diagnostics",
                                "list_mcp_server_status",
                                "list_loaded_threads",
                                "start_thread",
                                "create_thread_and_start_turn",
                                "resume_thread",
                                "start_turn",
                                "steer_turn",
                                "interrupt_turn",
                            }
                            removed_provisioning = {
                                "plan_remote_workspace",
                                "inspect_remote_workspace",
                                "canary_remote_workspace",
                                "provision_remote_workspace",
                                "bootstrap_remote_workspace",
                                "resume_remote_auth",
                                "begin_remote_auth",
                                "status_remote_workspace",
                            }
                            self.assertEqual(
                                set(tools),
                                required,
                                "Action Server catalog mismatch; "
                                f"missing={sorted(required - set(tools))}; "
                                f"actual={sorted(tools)}",
                            )
                            self.assertTrue(
                                removed_provisioning.isdisjoint(tools),
                                "Removed provisioning actions remain in the Action Server catalog",
                            )
                            for name in required:
                                schema = getattr(
                                    tools[name],
                                    "input_schema",
                                    getattr(tools[name], "inputSchema", None),
                                )
                                self.assertIsInstance(schema, dict)

                            def payload_properties(name: str) -> dict:
                                schema = getattr(
                                    tools[name],
                                    "input_schema",
                                    getattr(tools[name], "inputSchema", None),
                                )
                                properties = schema.get("properties", {})
                                self.assertIn(
                                    "payload",
                                    properties,
                                    f"{name} must expose the typed payload wrapper",
                                )
                                payload_schema = properties["payload"]
                                self.assertIsInstance(payload_schema, dict)
                                payload_properties = payload_schema.get("properties")
                                self.assertIsInstance(payload_properties, dict)
                                return payload_properties

                            callback_properties = payload_properties(
                                "create_thread_and_start_turn"
                            )
                            self.assertEqual(
                                callback_properties["enable_list_threads_callback"][
                                    "type"
                                ],
                                "boolean",
                            )
                            self.assertIs(
                                callback_properties["enable_list_threads_callback"][
                                    "default"
                                ],
                                False,
                            )
                            self.assertNotIn("dynamicTools", callback_properties)
                            turn_properties = payload_properties("start_turn")
                            self.assertIn("model", turn_properties)
                            self.assertIn("effort", turn_properties)
                            for name in (
                                "start_thread",
                                "resume_thread",
                                "start_turn",
                            ):
                                properties = payload_properties(name)
                                self.assertIn("model", properties)
                                self.assertIn("effort", properties)
                            self.assertIn(
                                "model_provider",
                                payload_properties("start_thread"),
                            )
                            self.assertIn(
                                "model_provider",
                                payload_properties("resume_thread"),
                            )

                            started = _result_data(
                                await session.call_tool(
                                    "create_thread_and_start_turn",
                                    {
                                        "payload": {
                                            "target": "local",
                                            "cwd": str(worktree),
                                            "text": "isolated typed MCP validation",
                                            "model": "fixture-model",
                                            "model_provider": "fixture-provider",
                                            "effort": "max",
                                            "wait_for_completion": True,
                                            "enable_list_threads_callback": True,
                                        }
                                    },
                                )
                            )
                            self.assertEqual(
                                started["result"]["created"]["thread"]["id"],
                                native.thread_id,
                            )
                            self.assertEqual(
                                started["result"]["completed"]["status"],
                                "completed",
                            )

                            read = _result_data(
                                await session.call_tool(
                                    "read_thread",
                                    {
                                        "payload": {
                                            "target": "local",
                                            "cwd": str(worktree),
                                            "thread_id": native.thread_id,
                                            "include_turns": True,
                                        }
                                    },
                                )
                            )
                            self.assertEqual(
                                read["result"]["thread"]["cwd"], str(worktree)
                            )

                try:
                    anyio.run(exercise)
                except (OSError, PermissionError) as error:
                    self.skipTest(
                        f"loopback MCP is unavailable in this runner: {error}"
                    )
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
                native.close()

            calls = list(native.calls)
            self.assertTrue(calls)
            methods = [call.get("method") for call in calls]
            self.assertIn("initialize", methods)
            self.assertIn("thread/start", methods)
            self.assertIn("turn/start", methods)
            self.assertIn("thread/read", methods)
            self.assertNotIn("thread/resume", methods)
            self.assertNotIn("command/exec", methods)
            self.assertNotIn("thread/shellCommand", methods)
            turn_starts = [call for call in calls if call.get("method") == "turn/start"]
            self.assertEqual(len(turn_starts), 1)
            self.assertEqual(turn_starts[0]["params"]["threadId"], native.thread_id)
            self.assertNotIn("model", turn_starts[0]["params"])
            self.assertEqual(turn_starts[0]["params"]["effort"], "max")
            thread_starts = [
                call for call in calls if call.get("method") == "thread/start"
            ]
            (namespace,) = thread_starts[0]["params"]["dynamicTools"]
            self.assertEqual(namespace["name"], "codex_app")
            self.assertEqual(namespace["type"], "namespace")
            (tool,) = namespace["tools"]
            self.assertEqual(tool["name"], "list_threads")
            self.assertEqual(tool["type"], "function")
            self.assertEqual(
                tool["inputSchema"]["properties"]["cwd"]["enum"], [str(worktree)]
            )
            self.assertEqual(thread_starts[0]["params"]["model"], "fixture-model")
            self.assertEqual(
                thread_starts[0]["params"]["modelProvider"], "fixture-provider"
            )

    def _wait_for_loopback(
        self, process: subprocess.Popen, port: int, server_log: Path
    ) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail(
                    "Action Server exited before loopback readiness: "
                    + server_log.read_text(errors="replace")[-4000:]
                )
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    return
            except PermissionError as error:
                self.skipTest(f"loopback MCP is blocked by the runner: {error}")
            except OSError:
                time.sleep(0.1)
        self.fail(
            "Action Server did not open its isolated loopback port: "
            + server_log.read_text(errors="replace")[-4000:]
        )


if __name__ == "__main__":
    unittest.main()
