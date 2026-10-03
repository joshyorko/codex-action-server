"""Container boundary checks without host daemons or operator credentials."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest


ROOT = Path(__file__).parents[1]


def launch(tmp_path, gateway, prepared_cache=None):
    target = tmp_path / "targets.json"
    target.write_text('{"targets":{"local":{"transport":"local"}}}')
    binary = tmp_path / "action-server"
    binary.write_text(
        "#!/usr/bin/env python3\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n"
    )
    binary.chmod(0o700)
    env = {
        **os.environ,
        "CODEX_ACTION_TARGETS": str(target),
        "CODEX_ACTION_RECEIPTS": str(tmp_path / "receipts"),
        "CODEX_ACTION_DATA": str(tmp_path / "runtime"),
        "ACTIONS_HOME": str(tmp_path / "actions"),
        "ACTION_SERVER_BIN": str(binary),
    }
    env.pop("CODEX_ACTION_BRIDGE_GATEWAY", None)
    env.pop("CODEX_ACTION_PREPARED_CACHE", None)
    if gateway is not None:
        env["CODEX_ACTION_BRIDGE_GATEWAY"] = gateway
    if prepared_cache is not None:
        env["CODEX_ACTION_PREPARED_CACHE"] = str(prepared_cache)
    return subprocess.run(
        ["bash", str(ROOT / "scripts/run-container.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_container_binds_only_explicit_private_gateway(tmp_path):
    result = launch(tmp_path, "172.30.86.1")
    assert result.returncode == 0, result.stderr
    command = json.loads(result.stdout)
    assert command[command.index("--address") + 1] == "172.30.86.1"
    assert command[command.index("--port") + 1] == "8088"
    assert "--expose" not in command
    assert (tmp_path / "receipts").stat().st_mode & 0o077 == 0
    assert (tmp_path / "runtime").stat().st_mode & 0o077 == 0
    assert (tmp_path / "actions").stat().st_mode & 0o077 == 0


def test_container_seeds_missing_cache_without_replacing_existing_state(tmp_path):
    cache = tmp_path / "baked-cache"
    cache.mkdir(mode=0o755)
    (cache / "existing").write_text("image value")
    (cache / "new").write_text("new cache entry")
    actions = tmp_path / "actions"
    actions.mkdir(mode=0o700)
    (actions / "existing").write_text("retained operator value")
    result = launch(tmp_path, "172.30.86.1", prepared_cache=cache)
    assert result.returncode == 0, result.stderr
    assert (actions / "existing").read_text() == "retained operator value"
    assert (actions / "new").read_text() == "new cache entry"
    assert actions.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize(
    "gateway",
    [None, "0.0.0.0", "8.8.8.8", "127.0.0.1", "::1", "localhost", "172.30.86.1;id"],
)
def test_container_rejects_missing_or_non_private_gateway(tmp_path, gateway):
    result = launch(tmp_path, gateway)
    assert result.returncode == 2
    assert not result.stdout


@pytest.mark.parametrize("sse", [False, True])
@pytest.mark.parametrize("catalog_valid", [False, True])
def test_health_catalog_contract_and_session_cleanup(tmp_path, sse, catalog_valid):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method = payload["method"]
            calls.append(method)
            if method == "initialize":
                result = {"protocolVersion": "2025-03-26", "capabilities": {}}
            elif self.headers.get("Mcp-Session-Id") != "catalog-session":
                self.send_error(400)
                return
            elif method == "notifications/initialized":
                self.send_response(202)
                self.end_headers()
                return
            else:
                names = [
                    "list_targets",
                    "inspect_target",
                    "read_dispatch_receipt",
                    "create_thread_and_start_turn",
                    *[f"other_{index}" for index in range(19)],
                ]
                if not catalog_valid:
                    names.pop()
                result = {"tools": [{"name": name} for name in names]}
            response = json.dumps(
                {"jsonrpc": "2.0", "id": payload["id"], "result": result}
            )
            if sse:
                response = "event: message\ndata: " + response + "\n\n"
            self.send_response(200)
            self.send_header("Mcp-Session-Id", "catalog-session")
            self.send_header(
                "Content-Type", "text/event-stream" if sse else "application/json"
            )
            self.end_headers()
            self.wfile.write(response.encode())

        def do_DELETE(self):
            calls.append("DELETE")
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/container_health.py")],
            env={
                **os.environ,
                "CODEX_ACTION_BRIDGE_GATEWAY": "127.0.0.1",
                "CODEX_ACTION_PORT": str(server.server_port),
            },
            text=True,
            capture_output=True,
            timeout=10,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert result.returncode == (0 if catalog_valid else 1), result.stderr
    assert calls == ["initialize", "notifications/initialized", "tools/list", "DELETE"]
