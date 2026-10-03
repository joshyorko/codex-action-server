"""Real runtime/MCP proof without needing a native daemon or Unix sockets."""

import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

import pytest


def test_standalone_http_boot_catalog_and_safe_diagnostics(tmp_path):
    binary = shutil.which("action-server")
    if not binary:
        pytest.skip("Actions Runtime CLI not installed")
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    package = Path(__file__).parents[1]
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = tmp_path / "targets.json"
    config.write_text(
        json.dumps(
            {
                "targets": {
                    "local": {
                        "transport": "local",
                        "socket_path": str(tmp_path / "does-not-exist.sock"),
                    }
                }
            }
        )
    )
    env = {
        **os.environ,
        "CODEX_ACTION_TARGETS": str(config),
        "CODEX_ACTION_RECEIPTS": str(tmp_path / "receipts"),
        "CODEX_HOME": str(tmp_path / "isolated-codex"),
    }
    with (tmp_path / "server.log").open("w") as log:
        proc = subprocess.Popen(
            [
                binary,
                "start",
                "--dir",
                str(package),
                "--datadir",
                str(tmp_path / "runtime"),
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
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail((tmp_path / "server.log").read_text())
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail(
                "Runtime startup timeout: " + (tmp_path / "server.log").read_text()
            )

        async def exercise():
            async with httpx.AsyncClient(trust_env=False, timeout=10) as http:
                async with streamable_http_client(
                    f"http://127.0.0.1:{port}/mcp", http_client=http
                ) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        catalog = await session.list_tools()
                        tools = {t.name: t for t in catalog.tools}
                        assert len(tools) == 23
                        assert {
                            "list_targets",
                            "inspect_target",
                            "read_dispatch_receipt",
                            "create_thread_and_start_turn",
                        } <= tools.keys()
                        schema = tools["create_thread_and_start_turn"].input_schema
                        assert "request_id" in json.dumps(schema)
                        r = await session.call_tool("list_targets", {})
                        assert not r.is_error
                        assert r.structured_content["result"]["targets"] == [
                            {"name": "local", "transport": "local"}
                        ]
                        r = await session.call_tool(
                            "inspect_target", {"payload": {"target": "unknown"}}
                        )
                        assert not r.is_error
                        assert r.structured_content["result"]["status"] == "unresolved"
                        r = await session.call_tool(
                            "read_dispatch_receipt",
                            {"payload": {"request_id": "not-created"}},
                        )
                        assert r.structured_content["result"]["state"] == "not_found"

        asyncio.run(exercise())
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
