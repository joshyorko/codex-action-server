#!/usr/bin/env python3
"""Stateless Codex App Server RPC client; invoke with Hermes terminal.

No target registry, scheduler, worker database, or model runtime. Requires
websockets>=15,<16. The selected target's native daemon owns all thread state.
"""

from __future__ import annotations
import argparse
from dataclasses import dataclass
import json
import math
import queue
import secrets
import threading
from pathlib import Path
from pathlib import PurePosixPath
import shlex
import socket
import subprocess
import sys
import time
from websockets.sync.client import connect, unix_connect
from websockets.exceptions import ConnectionClosed
from worker_containers import Worker

METHODS = {
    "mcpServerStatus/list",
    "model/list",
    "modelProvider/capabilities/read",
    "server/diagnostics",
    "thread/archive",
    "thread/compact/start",
    "thread/delete",
    "thread/fork",
    "thread/goal/clear",
    "thread/goal/get",
    "thread/goal/set",
    "thread/items/list",
    "thread/list",
    "thread/loaded/list",
    "thread/metadata/update",
    "thread/name/set",
    "thread/read",
    "thread/queue/add",
    "thread/queue/delete",
    "thread/queue/list",
    "thread/queue/reorder",
    "thread/queue/start",
    "thread/queue/update",
    "thread/revert",
    "thread/resume",
    "thread/search",
    "thread/searchOccurrences",
    "thread/timeline/list",
    "threadSection/list",
    "thread/settings/update",
    "thread/start",
    "thread/turns/list",
    "thread/unarchive",
    "turn/start",
    "turn/steer",
    "turn/interrupt",
    "turn/settings/update",
}

EXPERIMENTAL_METHODS = {
    "thread/search",
    "thread/searchOccurrences",
    "thread/timeline/list",
    "thread/queue/add",
    "thread/queue/list",
    "thread/queue/update",
    "thread/queue/delete",
    "thread/queue/reorder",
    "thread/queue/start",
}
EXPERIMENTAL_NATIVE_USER_AGENT = "codex-cli 0.160.1"

THREAD_ID_METHODS = {
    "thread/goal/clear",
    "thread/goal/get",
    "thread/goal/set",
    "thread/items/list",
    "thread/archive",
    "thread/compact/start",
    "thread/delete",
    "thread/fork",
    "thread/read",
    "thread/metadata/update",
    "thread/name/set",
    "thread/revert",
    "thread/queue/add",
    "thread/queue/delete",
    "thread/queue/list",
    "thread/queue/reorder",
    "thread/queue/start",
    "thread/queue/update",
    "thread/resume",
    "thread/searchOccurrences",
    "thread/settings/update",
    "thread/timeline/list",
    "thread/turns/list",
    "thread/unarchive",
    "turn/interrupt",
    "turn/settings/update",
    "turn/start",
    "turn/steer",
}

TURN_ID_METHODS = {
    "turn/settings/update",
    "turn/steer",
    "turn/interrupt",
}

OPTIONAL_THREAD_ID_METHODS = {"mcpServerStatus/list"}


class RpcError(RuntimeError):
    pass


@dataclass(frozen=True)
class Target:
    target: str
    codex_bin: str = "codex"
    socket_path: str | None = None
    logical_name: str | None = None
    ssh_options: tuple[str, ...] = ()
    workspace_uid: str | None = None
    workspace_id: str | None = None
    container: Worker | None = None

    def __post_init__(self):
        if (
            not self.target
            or self.target.startswith("-")
            or any(c.isspace() for c in self.target)
        ):
            raise ValueError(
                "Target must be local or an explicit SSH destination, not SSH options"
            )

    def command(self, args):
        if self.container is not None:
            return self.container.command(args)
        command = [self.codex_bin, *args]
        if self.target == "local":
            return command
        remote = shlex.join(command)
        if self.workspace_uid is not None:
            remote = (
                'test "$DEVSY_WORKSPACE_UID" = '
                + shlex.quote(self.workspace_uid)
                + ' && test "$DEVSY_WORKSPACE_ID" = '
                + shlex.quote(self.workspace_id or "")
                + " && exec "
                + remote
            )
        return [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "ForwardAgent=no",
            *self.ssh_options,
            self.target,
            remote,
        ]


class ApprovalBridge:
    """One callback only; no session grants or policy interpretation."""

    def __init__(self, client, thread_id, cwd, timeout=30):
        import secrets

        self.client, self.thread_id, self.cwd = client, thread_id, cwd
        self.timeout, self.nonce = timeout, secrets.token_hex(24)
        self.turn_id = self.start_request_id = self.pending = None
        self.used = False
        self.last_signal = None
        self.approval_elapsed = 0

    def execution_time(self):
        """Monotonic execution clock, paused only during the one approval."""
        now = time.monotonic()
        pending_elapsed = now - self.pending_since if self.pending else 0
        return now - self.approval_elapsed - pending_elapsed

    def handshake(self):
        # No output or connection before START; disable PTY echo so submitted
        # JSON cannot accidentally trigger watched marker substrings.
        if sys.stdin.isatty():
            import termios

            attrs = termios.tcgetattr(sys.stdin)
            attrs[3] &= ~termios.ECHO
            termios.tcsetattr(sys.stdin, termios.TCSANOW, attrs)
        self.inbox = queue.Queue()

        def stdin_reader():
            try:
                while True:
                    line = sys.stdin.readline(8193)
                    if not line or len(line) > 8192 or not line.endswith("\n"):
                        raise RpcError("Control stdin closed or oversized")
                    self.inbox.put(("stdin", line.rstrip("\r\n")))
            except Exception as error:
                self.inbox.put(("error", error))

        threading.Thread(target=stdin_reader, daemon=True).start()
        try:
            kind, value = self.inbox.get(timeout=60)
        except queue.Empty:
            raise TimeoutError("START handshake timed out") from None
        if kind != "stdin" or value != "START":
            raise RpcError("Expected START handshake")

    def start_io(self):
        self.io_started = True

        def websocket_reader():
            try:
                while True:
                    self.inbox.put(("ws", json.loads(self.client.ws.recv())))
            except Exception as error:
                self.inbox.put(("error", error))

        threading.Thread(target=websocket_reader, daemon=True).start()

    def receive(self, timeout):
        deadline = self.execution_time() + timeout
        while True:
            remaining = (
                self.deadline - time.monotonic()
                if self.pending
                else deadline - self.execution_time()
            )
            if remaining <= 0:
                raise TimeoutError("Approval or RPC deadline expired")
            try:
                kind, value = self.inbox.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError("Approval or RPC deadline expired") from None
            if kind == "error":
                raise value
            if kind == "stdin":
                self.decision(value)
            else:
                return value

    def signal(self, marker, context):
        # Watch-only Hermes drops hits inside its 15s cooldown. This is a
        # one-shot delay, NOT polling, and cannot defeat global/chunk limits.
        if self.last_signal is not None:
            time.sleep(max(0, 16 - (time.monotonic() - self.last_signal)))
        print(marker + " " + json.dumps(context, ensure_ascii=True), flush=True)
        self.last_signal = time.monotonic()

    def approval(self, message):
        p = message.get("params", {})
        if (
            self.used
            or self.start_request_id is None
            or message["method"] != "item/commandExecution/requestApproval"
            or p.get("kind", "command") != "command"
            or p.get("threadId") != self.thread_id
            or p.get("cwd") != self.cwd
            or not isinstance(p.get("turnId"), str)
            or not p["turnId"]
            or (self.turn_id is not None and p["turnId"] != self.turn_id)
            or not self.client.metadata.get("codexHome")
            or not isinstance(p.get("command"), str)
            or not p["command"]
            or not p.get("itemId")
            or p.get("networkApprovalContext")
            or p.get("additionalPermissions")
        ):
            raise RpcError("Unsupported or mismatched approval")
        self.turn_id = p["turnId"]
        identity = {
            "target": self.client.target.target,
            "codexHome": self.client.metadata["codexHome"],
            "cwd": self.cwd,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "server_request_id": message["id"],
            "connection_nonce": self.nonce,
        }
        self.pending = {"request": message, "identity": identity}
        self.used = True
        self.pending_since = time.monotonic()
        self.deadline = self.pending_since + self.timeout
        self.client.checkpoint()
        self.signal(
            "FRIDAY_CODEX_APPROVAL_REQUIRED",
            {
                "identity": identity,
                "command": p["command"][:2048],
                "command_truncated": len(p["command"]) > 2048,
                "item_id": p["itemId"],
                "allowed_decisions": ["accept", "cancel", "decline"],
            },
        )

    def decision(self, line):
        def unique_object(pairs):
            obj = {}
            for key, value in pairs:
                if key in obj:
                    raise RpcError("Ambiguous duplicate JSON key")
                obj[key] = value
            return obj

        reply = json.loads(line, object_pairs_hook=unique_object)
        if self.pending and time.monotonic() >= self.deadline:
            raise TimeoutError("Approval deadline expired")
        if (
            not self.pending
            or not isinstance(reply, dict)
            or set(reply) != {"identity", "decision"}
            or reply["identity"] != self.pending["identity"]
            or type(reply["identity"].get("server_request_id"))
            is not type(self.pending["identity"]["server_request_id"])
            or reply["decision"] not in ("accept", "cancel", "decline")
        ):
            raise RpcError("Invalid or stale approval decision")
        allowed = self.pending["request"]["params"].get("availableDecisions")
        if allowed is not None and reply["decision"] not in allowed:
            raise RpcError("Decision not offered by native server")
        response = {
            "id": self.pending["request"]["id"],
            "result": {"decision": reply["decision"]},
        }
        self.client.ws.send(json.dumps(response))
        self.client.receipts.append({**self.pending, "response": response})
        self.approval_elapsed += time.monotonic() - self.pending_since
        self.pending = None
        self.client.checkpoint()


class Client:
    def __init__(self, target: Target, timeout: float = 60):
        self.target, self.timeout = target, timeout
        self.proc = self.ws = None
        self.events, self.receipts = [], []
        self.next_id = 0
        self.metadata = {}
        self.socket_path = target.socket_path
        self.bridge = None
        self.evidence_path = None
        self.workstream = None
        self.connection_nonce = secrets.token_hex(24)
        self._handling_callback = False
        self._attached_threads = {}

    def set_workstream(self, cwd, thread_id, turn_id=None):
        self.workstream = {
            "target": self.target.logical_name or self.target.target,
            "codexHome": self.metadata.get("codexHome"),
            "cwd": cwd,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "originating_request_id": None,
        }

    def _validate_thread_result(self, result, thread_id=None):
        thread = result.get("thread") if isinstance(result, dict) else None
        if (
            not isinstance(thread, dict)
            or not isinstance(thread.get("id"), str)
            or not thread["id"]
            or not isinstance(thread.get("cwd"), str)
            or not thread["cwd"]
        ):
            raise RpcError("Native thread response is missing identity")
        if thread_id is not None and thread["id"] != thread_id:
            raise RpcError("Native thread identity mismatch")
        if self.workstream is not None:
            if thread["id"] != self.workstream["thread_id"]:
                raise RpcError("Native thread identity mismatch")
            if thread["cwd"] != self.workstream["cwd"]:
                raise RpcError("Native thread/cwd identity mismatch")
        return result

    def _remember_thread_start(self, result, params):
        self._validate_thread_result(result)
        thread = result["thread"]
        requested_cwd = params.get("cwd")
        if requested_cwd is not None and thread["cwd"] != requested_cwd:
            raise RpcError("Native thread/start cwd identity mismatch")
        self._attached_threads[thread["id"]] = result

    def ensure_thread_attached(self, thread_id, params, timeout=None):
        """Attach this connection without resuming an unmaterialized thread.

        Native ``thread/start`` already attaches a fresh thread to the
        creating connection, but it has no rollout for a later
        ``thread/resume`` to load. Only the attach-only resume shape used by
        the bounded lifecycle can be satisfied locally; all other overrides
        fail closed rather than being silently discarded.
        """
        if not isinstance(thread_id, str) or not thread_id:
            raise ValueError("Thread attachment requires a thread id")
        if not isinstance(params, dict) or params.get("threadId") != thread_id:
            raise ValueError("Thread attachment identity mismatch")
        if self.workstream is not None and self.workstream["thread_id"] != thread_id:
            raise RpcError("Native thread identity mismatch")
        requested_cwd = params.get("cwd")
        if self.workstream is not None and requested_cwd is not None:
            if requested_cwd != self.workstream["cwd"]:
                raise ValueError("Requested cwd does not match workstream")
        if set(params) - {"threadId", "excludeTurns", "cwd"}:
            raise RpcError(
                "Fresh-thread attachment accepts only threadId, excludeTurns, and cwd"
            )
        if params.get("excludeTurns") is not True:
            raise RpcError("Fresh-thread attachment requires excludeTurns:true")

        attached = self._attached_threads.get(thread_id)
        if attached is not None:
            self._validate_thread_result(attached, thread_id)
            if requested_cwd is not None and attached["thread"]["cwd"] != requested_cwd:
                raise RpcError("Native thread/cwd identity mismatch")
            return attached

        result = self.request("thread/resume", params, timeout)
        self._validate_thread_result(result, thread_id)
        self._attached_threads[thread_id] = result
        return result

    def _validate_request(self, method, params):
        if method == "thread/list":
            if not isinstance(params, dict):
                raise ValueError("thread/list params must be an object")
            if self.workstream is None or params.get("cwd") is None:
                return
            requested_cwd = params["cwd"]
            if requested_cwd not in (self.workstream["cwd"], [self.workstream["cwd"]]):
                raise RpcError("Native thread/cwd identity mismatch")
            return
        if method in OPTIONAL_THREAD_ID_METHODS:
            if not isinstance(params, dict):
                raise ValueError(method + " params must be an object")
            thread_id = params.get("threadId")
            if thread_id is not None and (
                not isinstance(thread_id, str) or not thread_id
            ):
                raise ValueError(method + " threadId must be non-empty")
            if (
                self.workstream is not None
                and thread_id is not None
                and thread_id != self.workstream["thread_id"]
            ):
                raise RpcError("Native thread identity mismatch")
            return
        if method not in THREAD_ID_METHODS:
            return
        if not isinstance(params, dict):
            raise ValueError(method + " params must be an object")
        thread_id = params.get("threadId")
        if not isinstance(thread_id, str) or not thread_id:
            raise ValueError(method + " requires a threadId")
        turn_id = None
        if method in TURN_ID_METHODS:
            turn_id_field = "expectedTurnId" if method == "turn/steer" else "turnId"
            turn_id = params.get(turn_id_field)
            if not isinstance(turn_id, str) or not turn_id:
                raise ValueError(method + " requires a " + turn_id_field)
        if self.workstream is None:
            return
        if thread_id != self.workstream["thread_id"]:
            raise RpcError("Native thread identity mismatch")
        requested_cwd = params.get("cwd")
        if requested_cwd is not None and requested_cwd != self.workstream["cwd"]:
            raise RpcError("Native thread/cwd identity mismatch")
        expected_turn_id = self.workstream.get("turn_id")
        if method in TURN_ID_METHODS and expected_turn_id is not None:
            if turn_id != expected_turn_id:
                raise RpcError("Native turn identity mismatch")

    def checkpoint(self):
        """Publish correlation evidence before asking for human input."""
        if self.evidence_path is not None:
            payload = {
                "connection": self.provenance(),
                "receipts": self.receipts,
                "events": self.events,
                "workstream": self.workstream,
                "pending_approval": self.bridge.pending if self.bridge else None,
                "turn_id": self.bridge.turn_id if self.bridge else None,
            }
            self.evidence_path.write_text(json.dumps(payload, indent=2))

    def __enter__(self):
        if self.socket_path is None:
            probe = subprocess.run(
                self.target.command(["app-server", "daemon", "version"]),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=True,
            )
            version = json.loads(probe.stdout)
            if version.get("status") != "running" or not version.get("socketPath"):
                raise RpcError(
                    "Selected target has no running native App Server daemon; no auto-start performed"
                )
            self.socket_path = version["socketPath"]
        try:
            if self.target.target == "local":
                self.ws = unix_connect(
                    self.socket_path,
                    compression=None,
                    open_timeout=self.timeout,
                    max_size=64 * 1024 * 1024,
                )
            else:
                local, child = socket.socketpair()
                try:
                    self.proc = subprocess.Popen(
                        self.target.command(
                            ["app-server", "proxy", "--sock", self.socket_path]
                        ),
                        stdin=child.fileno(),
                        stdout=child.fileno(),
                        stderr=sys.stderr,
                    )
                except BaseException:
                    local.close()
                    raise
                finally:
                    child.close()
                try:
                    self.ws = connect(
                        "ws://localhost/",
                        sock=local,
                        unix=True,
                        proxy=None,
                        compression=None,
                        open_timeout=self.timeout,
                        max_size=64 * 1024 * 1024,
                    )
                except BaseException:
                    local.close()
                    raise
            self.metadata = self.request(
                "initialize",
                {
                    "clientInfo": {"name": "friday-external-codex", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            if (
                self.target.container is not None
                and self.metadata.get("codexHome") != self.target.container.codex_home
            ):
                raise RpcError("Native Codex home identity mismatch")
            if self.workstream is not None:
                self.workstream["codexHome"] = self.metadata.get("codexHome")
            self.ws.send(json.dumps({"method": "initialized"}))
            return self
        except BaseException:
            self.close()
            raise

    def receive(self, timeout):
        message = (
            self.bridge.receive(timeout)
            if self.bridge and getattr(self.bridge, "io_started", False)
            else json.loads(self.ws.recv(timeout=timeout))
        )
        if "method" in message:
            self.events.append(message)
        if self.bridge and message.get("method") == "turn/started":
            params = message.get("params", {})
            if (
                params.get("threadId") == self.bridge.thread_id
                and self.bridge.start_request_id is not None
            ):
                turn_id = params.get("turn", {}).get("id")
                if not turn_id or self.bridge.turn_id not in (None, turn_id):
                    raise RpcError("Native turn/started identity mismatch")
                self.bridge.turn_id = turn_id
        if self.workstream and message.get("method") == "turn/started":
            params = message.get("params", {})
            if params.get("threadId") == self.workstream["thread_id"]:
                turn_id = params.get("turn", {}).get("id")
                if not isinstance(turn_id, str) or not turn_id:
                    raise RpcError("Native turn/started identity mismatch")
                if (
                    self.workstream["turn_id"] is not None
                    and self.workstream["turn_id"] != turn_id
                ):
                    raise RpcError("Native turn/started identity mismatch")
                self.workstream["turn_id"] = turn_id
        if "method" in message and "id" in message:
            if message["method"] == "item/tool/call":
                try:
                    self._client_tool_call(message)
                except Exception:
                    self.ws.send(
                        json.dumps(
                            {
                                "id": message["id"],
                                "error": {
                                    "code": -32601,
                                    "message": "Unsupported or mismatched client tool",
                                },
                            }
                        )
                    )
                    raise
                return {}
            if self.bridge:
                try:
                    self.bridge.approval(message)
                except Exception:
                    self.ws.send(
                        json.dumps(
                            {
                                "id": message["id"],
                                "error": {
                                    "code": -32601,
                                    "message": "Unsupported or mismatched approval/input",
                                },
                            }
                        )
                    )
                    raise
                return {}
            # Never silently approve execution, permissions, or user-input requests.
            self.ws.send(
                json.dumps(
                    {
                        "id": message["id"],
                        "error": {
                            "code": -32601,
                            "message": "Human approval/input is not implemented by this bounded client",
                        },
                    }
                )
            )
            raise RpcError("Server requires approval/input: " + message["method"])
        return message

    def _callback_identity(self, message):
        params = message.get("params", {})
        return {
            **self.workstream,
            "codexBin": self.target.codex_bin,
            "socket": self.socket_path,
            "server": self.metadata.get("userAgent"),
            "server_request_id": message.get("id"),
            "call_id": params.get("callId"),
            "connection_nonce": self.connection_nonce,
        }

    def _send_client_tool_failure(self, message, identity, detail):
        response = {
            "id": message["id"],
            "result": {
                "success": False,
                "contentItems": [{"type": "inputText", "text": detail}],
            },
        }
        self.ws.send(json.dumps(response))
        self.receipts.append(
            {
                "identity": identity,
                "response": response,
                "outcome": "client_tool_failed",
            }
        )

    def _client_tool_call(self, message):
        if self._handling_callback:
            raise RpcError("Nested client tool callback rejected")
        params = message.get("params")
        if not isinstance(params, dict) or not self.workstream:
            raise RpcError("mismatched client tool callback")
        identity = self._callback_identity(message)
        if (
            params.get("threadId") != self.workstream["thread_id"]
            or not isinstance(params.get("turnId"), str)
            or not params["turnId"]
            or (
                self.workstream["turn_id"] is not None
                and params["turnId"] != self.workstream["turn_id"]
            )
            or self.workstream.get("originating_request_id") is None
            or not self.metadata.get("codexHome")
            or not isinstance(params.get("callId"), str)
            or not params["callId"]
        ):
            raise RpcError("mismatched client tool callback identity")
        if self.workstream["turn_id"] is None:
            self.workstream["turn_id"] = params["turnId"]
            identity["turn_id"] = params["turnId"]
        if (
            params.get("namespace") != "codex_app"
            or params.get("tool") != "list_threads"
        ):
            self._send_client_tool_failure(
                message,
                identity,
                f"Unsupported client tool: {params.get('namespace')}.{params.get('tool')}; "
                'only codex_app.list_threads is implemented',
            )
            return
        arguments = params.get("arguments")
        if not isinstance(arguments, dict):
            self._send_client_tool_failure(
                message, identity, "codex_app.list_threads arguments must be an object"
            )
            return
        allowed = {"cwd", "cursor", "limit"}
        if set(arguments) - allowed:
            self._send_client_tool_failure(
                message, identity, "codex_app.list_threads received unsupported filters"
            )
            return
        native = {}
        if "limit" in arguments:
            if (
                type(arguments["limit"]) is not int
                or not 1 <= arguments["limit"] <= 100
            ):
                self._send_client_tool_failure(
                    message, identity, "codex_app.list_threads limit must be 1..100"
                )
                return
            native["limit"] = arguments["limit"]
        if "cursor" in arguments:
            if not isinstance(arguments["cursor"], str) or not arguments["cursor"]:
                self._send_client_tool_failure(
                    message, identity, "codex_app.list_threads cursor must be non-empty"
                )
                return
            native["cursor"] = arguments["cursor"]
        if "cwd" in arguments:
            cwd = arguments["cwd"]
            cwds = [cwd] if isinstance(cwd, str) else cwd
            if (
                not isinstance(cwds, list)
                or not cwds
                or any(
                    not isinstance(item, str)
                    or not PurePosixPath(item).is_absolute()
                    or ".." in PurePosixPath(item).parts
                    for item in cwds
                )
                or any(item != self.workstream["cwd"] for item in cwds)
            ):
                self._send_client_tool_failure(
                    message,
                    identity,
                    "codex_app.list_threads cwd is outside the selected workstream",
                )
                return
            native["cwd"] = cwd
        self._handling_callback = True
        try:
            actual = self.request(
                "thread/read",
                {"threadId": self.workstream["thread_id"], "includeTurns": False},
            )
            thread = actual.get("thread") if isinstance(actual, dict) else None
            if (
                not isinstance(thread, dict)
                or thread.get("id") != self.workstream["thread_id"]
                or thread.get("cwd") != self.workstream["cwd"]
            ):
                raise RpcError("Mismatched client tool thread/cwd identity")
            result = self.request("thread/list", native)
            output = {
                "success": True,
                "contentItems": [
                    {
                        "type": "inputText",
                        "text": json.dumps(result, ensure_ascii=True),
                    }
                ],
            }
            response = {"id": message["id"], "result": output}
            self.ws.send(json.dumps(response))
            self.receipts.append(
                {
                    "identity": identity,
                    "response": response,
                    "outcome": "client_tool_completed",
                }
            )
        except (RpcError, OSError, TimeoutError, ValueError, KeyError) as error:
            self._send_client_tool_failure(
                message, identity, f"codex_app.list_threads failed closed: {error}"
            )
        finally:
            self._handling_callback = False

    def execution_time(self):
        return self.bridge.execution_time() if self.bridge else time.monotonic()

    def request(self, method, params, timeout=None):
        if method not in METHODS | {"initialize"}:
            raise ValueError("Method outside the typed native surface: " + method)
        if (
            method in EXPERIMENTAL_METHODS
            and self.metadata.get("userAgent") != EXPERIMENTAL_NATIVE_USER_AGENT
        ):
            raise RpcError(
                method + " requires the pinned experimental Codex 0.160.1 schema"
            )
        self._validate_request(method, params)
        self.next_id += 1
        msg = {"id": self.next_id, "method": method, "params": params}
        if self.workstream and method == "turn/start":
            self.workstream["originating_request_id"] = msg["id"]
        if self.bridge and method == "turn/start":
            self.bridge.start_request_id = msg["id"]
        self.ws.send(json.dumps(msg))
        deadline = self.execution_time() + (
            self.timeout if timeout is None else timeout
        )
        while True:
            remaining = deadline - self.execution_time()
            if remaining <= 0:
                raise TimeoutError(
                    method + " timed out; inspect state before retrying a mutation"
                )
            response = self.receive(remaining)
            if response.get("id") == msg["id"]:
                self.receipts.append({"request": msg, "response": response})
                if "error" in response:
                    raise RpcError(json.dumps(response["error"]))
                result = response["result"]
                if method == "thread/start":
                    self._remember_thread_start(result, params)
                if self.bridge and method == "turn/start":
                    turn_id = result["turn"]["id"]
                    if self.bridge.turn_id not in (None, turn_id):
                        raise RpcError("Native turn/start identity mismatch")
                    self.bridge.turn_id = turn_id
                if self.workstream and method == "turn/start":
                    turn_id = result["turn"]["id"]
                    if (
                        self.workstream["turn_id"] is not None
                        and self.workstream["turn_id"] != turn_id
                    ):
                        raise RpcError("Native turn/start identity mismatch")
                    self.workstream["turn_id"] = turn_id
                return result

    def wait_turn(self, thread_id, turn_id, timeout=300):
        deadline = self.execution_time() + timeout
        cursor = 0
        while True:
            for event in self.events[cursor:]:
                params = event.get("params", {})
                if (
                    event.get("method") == "turn/completed"
                    and params.get("threadId") == thread_id
                    and params.get("turn", {}).get("id") == turn_id
                ):
                    return params["turn"]
            cursor = len(self.events)
            remaining = deadline - self.execution_time()
            if remaining <= 0:
                raise TimeoutError("Turn remains unverified; do not resubmit blindly")
            self.receive(remaining)

    @staticmethod
    def _turn_from_thread(result, thread_id, turn_id):
        thread = result.get("thread") if isinstance(result, dict) else None
        if not isinstance(thread, dict) or thread.get("id") != thread_id:
            raise RpcError("Native thread identity mismatch")
        turns = thread.get("turns")
        if not isinstance(turns, list):
            return None
        for turn in turns:
            if isinstance(turn, dict) and turn.get("id") == turn_id:
                return turn
        return None

    def subscribe_turn(self, expected_cwd, thread_id, turn_id, deadline=300):
        """Attach to one existing turn and consume only its terminal outcome.

        Resume is deliberately first: thread/read is a state read and does not
        subscribe a connection. The read then closes the missed-completion gap
        for a turn that finished before or during attachment.
        """
        if not isinstance(expected_cwd, str) or not expected_cwd.startswith("/"):
            raise ValueError("Subscription requires an absolute expected cwd")
        if not isinstance(thread_id, str) or not thread_id:
            raise ValueError("Subscription requires a thread id")
        if not isinstance(turn_id, str) or not turn_id:
            raise ValueError("Subscription requires a turn id")
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline)
            or not 0 < deadline <= 3600
        ):
            raise ValueError("Subscription deadline must be finite seconds in 0..3600")
        end = time.monotonic() + deadline
        identity = {
            "target": self.target.logical_name or self.target.target,
            "cwd": expected_cwd,
            "thread_id": thread_id,
            "turn_id": turn_id,
        }
        self.receipts.append(
            {"operation": "turn/subscribe", "identity": identity, "status": "attaching"}
        )

        def terminal_result(turn):
            if isinstance(turn, dict) and turn.get("status") in {
                "completed",
                "interrupted",
                "failed",
            }:
                result = {"thread_id": thread_id, "turn_id": turn_id, **turn}
                self.receipts.append(
                    {
                        "operation": "turn/subscribe",
                        "identity": identity,
                        "status": result["status"],
                    }
                )
                return result
            return None

        def reconcile():
            """Read exact native state once after watch expiry or idle timeout."""
            try:
                state = self.request(
                    "thread/read",
                    {"threadId": thread_id, "includeTurns": True},
                    timeout=max(0.1, min(1.0, deadline)),
                )
                thread = state.get("thread") if isinstance(state, dict) else None
                if (
                    not isinstance(thread, dict)
                    or thread.get("id") != thread_id
                    or thread.get("cwd") != expected_cwd
                ):
                    raise RpcError(
                        "Native thread/cwd identity mismatch during reconciliation"
                    )
                result = terminal_result(
                    self._turn_from_thread(state, thread_id, turn_id)
                )
                if result is not None:
                    return result
                for event in self.events:
                    params = event.get("params", {})
                    event_turn = params.get("turn", {})
                    if (
                        params.get("threadId") == thread_id
                        and event_turn.get("id") == turn_id
                    ):
                        result = terminal_result(event_turn)
                        if result is not None:
                            return result
            except (TimeoutError, OSError, ConnectionClosed):
                return None
            return None

        def timed_out(error):
            result = {
                "thread_id": thread_id,
                "turn_id": turn_id,
                "status": "timed_out",
            }
            self.receipts.append(
                {
                    "operation": "turn/subscribe",
                    "identity": identity,
                    "status": "timed_out",
                    "error": str(error),
                }
            )
            return result

        try:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "Turn subscription deadline expired; outcome unverified"
                )
            self.request(
                "thread/resume",
                {"threadId": thread_id, "excludeTurns": True},
                remaining,
            )
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "Turn subscription deadline expired; outcome unverified"
                )
            state = self.request(
                "thread/read", {"threadId": thread_id, "includeTurns": True}, remaining
            )
            thread = state.get("thread") if isinstance(state, dict) else None
            if (
                not isinstance(thread, dict)
                or thread.get("id") != thread_id
                or thread.get("cwd") != expected_cwd
            ):
                raise RpcError("Native thread/cwd identity mismatch")
            turn = self._turn_from_thread(state, thread_id, turn_id)
            if turn is None:
                buffered = [
                    event
                    for event in self.events
                    if event.get("params", {}).get("threadId") == thread_id
                    and event.get("params", {}).get("turn", {}).get("id") == turn_id
                    and event.get("method")
                    in {"turn/completed", "turn/interrupted", "turn/failed"}
                ]
                if not buffered:
                    raise RpcError("Native turn not found; outcome unverified")
            result = terminal_result(turn)
            if result is not None:
                return result
            cursor = 0
            while True:
                for event in self.events[cursor:]:
                    params = event.get("params", {})
                    event_turn = params.get("turn", {})
                    status = event_turn.get("status")
                    if (
                        event.get("method")
                        in {"turn/completed", "turn/interrupted", "turn/failed"}
                        and params.get("threadId") == thread_id
                        and event_turn.get("id") == turn_id
                        and status in {"completed", "interrupted", "failed"}
                    ):
                        result = {
                            "thread_id": thread_id,
                            "turn_id": turn_id,
                            **event_turn,
                        }
                        self.receipts.append(
                            {
                                "operation": "turn/subscribe",
                                "identity": identity,
                                "status": status,
                            }
                        )
                        return result
                cursor = len(self.events)
                remaining = end - time.monotonic()
                if remaining <= 0:
                    result = reconcile()
                    return (
                        result
                        if result is not None
                        else timed_out(
                            TimeoutError(
                                "Turn subscription deadline expired; outcome unverified"
                            )
                        )
                    )
                try:
                    self.receive(remaining)
                except TimeoutError as error:
                    if time.monotonic() < end:
                        self.receipts.append(
                            {
                                "operation": "turn/subscribe",
                                "identity": identity,
                                "status": "transport_idle",
                                "error": str(error),
                            }
                        )
                        continue
                    result = reconcile()
                    return result if result is not None else timed_out(error)
        except TimeoutError as error:
            # Attachment has not established the exact native turn yet. Only
            # the watch loop may reconcile an already verified subscription.
            self.receipts.append(
                {
                    "operation": "turn/subscribe",
                    "identity": identity,
                    "status": "timeout",
                    "error": str(error),
                }
            )
            raise
        except (OSError, ConnectionClosed) as error:
            self.receipts.append(
                {
                    "operation": "turn/subscribe",
                    "identity": identity,
                    "status": "disconnect",
                    "error": str(error),
                }
            )
            raise
        except RpcError as error:
            self.receipts.append(
                {
                    "operation": "turn/subscribe",
                    "identity": identity,
                    "status": "identity_mismatch",
                    "error": str(error),
                }
            )
            raise

    def provenance(self):
        return {
            "target": self.target.logical_name or self.target.target,
            "codexBin": self.target.codex_bin,
            "socket": self.socket_path,
            "codexHome": self.metadata.get("codexHome"),
            "server": self.metadata.get("userAgent"),
        }

    def close(self):
        # Only the transient proxy owned by this connection, never the daemon.
        ws, proc = self.ws, self.proc
        self.ws = self.proc = None
        try:
            if ws is not None:
                ws.close()
        finally:
            if proc is not None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

    def __exit__(self, *_):
        if self.bridge and self.bridge.pending:
            pending = self.bridge.pending
            response = {
                "id": pending["request"]["id"],
                "result": {"decision": "cancel"},
            }
            try:
                self.ws.send(json.dumps(response))
                outcome = "fail_closed_cancel_sent"
            except Exception:
                outcome = "disconnected_cancel_unconfirmed"
            self.receipts.append({**pending, "response": response, "outcome": outcome})
            self.bridge.pending = None
        self.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target", required=True, help="local or SSH destination from the workstream"
    )
    parser.add_argument(
        "--codex-bin", default="codex", help="Verified Codex executable on that target"
    )
    parser.add_argument(
        "--socket", help="Optional verified socket; otherwise ask native daemon version"
    )
    parser.add_argument(
        "--request-file",
        type=Path,
        required=True,
        help="JSON with method, params, optional expected_cwd and wait_turn",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Machine-local evidence file, not a session database",
    )
    parser.add_argument(
        "--approval-bridge",
        action="store_true",
        help="Opt in to one command approval; requires START on stdin and wait_turn",
    )
    parser.add_argument(
        "--approval-timeout",
        type=float,
        default=30,
        help="Command approval timeout in seconds (1..600; default: 30)",
    )
    args = parser.parse_args()
    if not 1 <= args.approval_timeout <= 600:
        parser.error("--approval-timeout must be finite seconds in 1..600")
    request = json.loads(args.request_file.read_text())
    result = None
    client = Client(Target(args.target, args.codex_bin, args.socket))
    client.evidence_path = args.output
    try:
        if args.approval_bridge:
            client.bridge = ApprovalBridge(
                client,
                request.get("params", {}).get("threadId"),
                request.get("expected_cwd"),
                timeout=args.approval_timeout,
            )
            client.bridge.handshake()
            if (
                request.get("method") != "turn/start"
                or request.get("wait_turn") is not True
            ):
                raise ValueError(
                    "Approval bridge requires turn/start with wait_turn:true"
                )
        with client:
            if client.bridge:
                client.bridge.start_io()
            method, params = request["method"], request.get("params", {})
            if method == "turn/subscribe":
                expected = request.get("expected_cwd")
                thread_id, turn_id = params.get("threadId"), params.get("turnId")
                if not expected or not thread_id or not turn_id:
                    raise ValueError(
                        "turn/subscribe requires expected_cwd, threadId, and turnId"
                    )
                client.set_workstream(expected, thread_id, turn_id)
                result = client.subscribe_turn(
                    expected, thread_id, turn_id, request.get("deadline", 300)
                )
                method = None
            if method == "turn/start":
                client.set_workstream(
                    request.get("expected_cwd"), params.get("threadId")
                )
            if method in {
                "thread/goal/set",
                "thread/goal/clear",
                "thread/settings/update",
                "turn/settings/update",
                "thread/resume",
                "turn/start",
                "turn/steer",
                "turn/interrupt",
            }:
                expected = request.get("expected_cwd")
                if not expected or not params.get("threadId"):
                    raise ValueError(
                        "Thread mutation requires expected_cwd and threadId alongside target"
                    )
                if params.get("cwd") is not None and params["cwd"] != expected:
                    raise ValueError(
                        "Requested cwd does not match workstream; refusing mutation"
                    )
                actual = client.request(
                    "thread/read",
                    {"threadId": params["threadId"], "includeTurns": False},
                )
                if actual["thread"]["cwd"] != expected:
                    raise ValueError(
                        "Thread cwd does not match workstream; refusing mutation"
                    )
                client._validate_thread_result(actual, params["threadId"])
            if method == "turn/start":
                # Resume subscribes this connection even when the thread is already loaded.
                client.request(
                    "thread/resume",
                    {"threadId": params["threadId"], "excludeTurns": True},
                )
            if method is not None:
                result = client.request(method, params)
            if request.get("wait_turn"):
                result = {
                    "accepted": result,
                    "completed": client.wait_turn(
                        params["threadId"], result["turn"]["id"]
                    ),
                }
        payload = {
            "connection": client.provenance(),
            "workstream": client.workstream,
            "result": result,
            "receipts": client.receipts,
            "events": client.events,
        }
        args.output.write_text(json.dumps(payload, indent=2))
        if client.bridge:
            client.bridge.signal(
                "FRIDAY_CODEX_TURN_COMPLETED",
                {
                    "connection": client.provenance(),
                    "thread_id": client.bridge.thread_id,
                    "turn_id": client.bridge.turn_id,
                    "status": result["completed"].get("status"),
                    "connection_nonce": client.bridge.nonce,
                    "evidence": str(args.output),
                },
            )
        else:
            print(
                json.dumps(
                    {
                        "connection": client.provenance(),
                        "result": result,
                        "evidence": str(args.output),
                    }
                )
            )
    except Exception as error:
        args.output.write_text(
            json.dumps(
                {
                    "connection": client.provenance(),
                    "workstream": client.workstream,
                    "error": str(error),
                    "receipts": client.receipts,
                    "events": client.events,
                },
                indent=2,
            )
        )
        if client.bridge:
            client.bridge.signal(
                "FRIDAY_CODEX_BRIDGE_FAILED",
                {
                    "error_type": type(error).__name__,
                    "evidence": str(args.output),
                    "connection_nonce": client.bridge.nonce,
                    "turn_id": client.bridge.turn_id,
                },
            )
            raise SystemExit(1) from None
        raise


if __name__ == "__main__":
    main()
