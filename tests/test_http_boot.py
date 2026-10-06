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

from action_catalog_contract import action_names_for_profile
from test_thread_discovery import assert_mcp_cwd_rejected


@pytest.mark.parametrize("profile", ["operator", "observe"])
def test_standalone_http_boot_catalog_and_safe_diagnostics(tmp_path, profile):
    binary = shutil.which("action-server")
    if not binary:
        pytest.skip("Actions Runtime CLI not installed")
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared.exceptions import MCPError

    package = Path(__file__).parents[1]
    native = None
    thread_id = "thread-1"
    cwd = "/work"
    socket_path = tmp_path / "does-not-exist.sock"
    if profile == "observe":
        from test_action_server_validation import NativeFixture, _result_data

        worktree = tmp_path / "worktree"
        codex_home = tmp_path / "codex-home"
        worktree.mkdir()
        codex_home.mkdir()
        native = NativeFixture(tmp_path / "native.sock", worktree, codex_home)
        thread_id, cwd, socket_path = native.thread_id, native.cwd, native.socket_path
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
                        "socket_path": str(socket_path),
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
        "CODEX_ACTION_DATA": str(tmp_path / "runtime"),
        "CODEX_ACTION_PORT": str(port),
        "CODEX_ACTION_PROFILE": profile,
        "ACTION_SERVER_BIN": binary,
    }
    with (tmp_path / "server.log").open("w") as log:
        proc = subprocess.Popen(
            ["bash", str(package / "scripts/run.sh")],
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
                        assert set(tools) == action_names_for_profile(profile)
                        assert {
                            "list_targets",
                            "inspect_target",
                            "read_dispatch_receipt",
                            "get_thread_snapshot",
                        } <= tools.keys()
                        if profile == "operator":
                            assert "create_thread_and_start_turn" in tools
                            schema = tools["create_thread_and_start_turn"].input_schema
                            assert "request_id" in json.dumps(schema)
                        else:
                            assert "create_thread_and_start_turn" not in tools
                            try:
                                forbidden = await session.call_tool(
                                    "start_turn",
                                    {
                                        "payload": {
                                            "target": "local",
                                            "cwd": cwd,
                                            "thread_id": thread_id,
                                            "text": "must not reach native Codex",
                                            "profile": "operator",
                                        }
                                    },
                                )
                            except MCPError:
                                pass
                            else:
                                assert forbidden.is_error
                        discovery = tools["discover_threads"].input_schema[
                            "properties"
                        ]["payload"]
                        assert "cwd" in discovery["required"]
                        assert discovery["properties"]["cwd"]["type"] == "string"
                        for scope in (
                            {},
                            {"cwd": None},
                            {"cwd": ""},
                            {"cwd": "relative/path"},
                        ):
                            await assert_mcp_cwd_rejected(
                                session,
                                "discover_threads",
                                {"target": "local", **scope},
                            )
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
                        if profile == "observe":
                            read = _result_data(
                                await session.call_tool(
                                    "read_thread",
                                    {
                                        "payload": {
                                            "target": "local",
                                            "cwd": cwd,
                                            "thread_id": thread_id,
                                            "include_turns": False,
                                        }
                                    },
                                )
                            )
                            assert read["result"]["thread"]["id"] == thread_id
                            assert read["result"]["thread"]["cwd"] == cwd
                            direct_read = await http.post(
                                f"http://127.0.0.1:{port}/api/actions/"
                                "codex-action-server/read-thread/run",
                                json={
                                    "payload": {
                                        "target": "local",
                                        "cwd": cwd,
                                        "thread_id": thread_id,
                                        "include_turns": False,
                                    }
                                },
                            )
                            assert direct_read.status_code == 200
                            direct_write = await http.post(
                                f"http://127.0.0.1:{port}/api/actions/"
                                "codex-action-server/start-turn/run",
                                json={
                                    "payload": {
                                        "target": "local",
                                        "cwd": cwd,
                                        "thread_id": thread_id,
                                        "text": "must not reach native Codex",
                                        "profile": "operator",
                                    }
                                },
                            )
                            assert direct_write.status_code == 404

        asyncio.run(exercise())
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        if native is not None:
            native.close()

    if native is not None:
        assert [call.get("method") for call in native.calls].count("thread/read") == 2
        assert not {call.get("method") for call in native.calls}.intersection(
            {
                "thread/start",
                "thread/resume",
                "turn/start",
                "turn/steer",
                "turn/interrupt",
            }
        )
