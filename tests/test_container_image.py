"""Run the production image against a disposable native Unix-socket fixture."""

import asyncio
import json
import os
from pathlib import Path
import runpy
import subprocess
import time
import uuid

import pytest

from test_action_server_validation import NativeFixture


IMAGE = os.environ.get("CODEX_ACTION_CONTAINER_IMAGE")
pytestmark = pytest.mark.skipif(
    not IMAGE,
    reason="Set CODEX_ACTION_CONTAINER_IMAGE to run the production image proof",
)


def docker(*args, check=True):
    return subprocess.run(
        ["docker", *args], text=True, capture_output=True, check=check, timeout=120
    )


def test_production_image_mcp_native_socket_and_persistent_receipt(tmp_path):
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    options = json.loads(docker("info", "--format", "{{json .SecurityOptions}}").stdout)
    assert not any(
        "rootless" in option or "userns" in option for option in options
    ), "Existing host loopback/socket access requires rootful Docker without UID remapping"

    identity = "cas-fixture-" + uuid.uuid4().hex[:12]
    gateway = "172.30.186.1"
    native_dir = tmp_path / "native"
    native_dir.mkdir(mode=0o750)
    native_socket = native_dir / "native.sock"
    native = NativeFixture(native_socket, tmp_path, tmp_path / "native-home")
    native_socket.chmod(0o660)
    target = tmp_path / "targets.json"
    target.write_text(
        json.dumps(
            {
                "targets": {
                    "local": {
                        "transport": "local",
                        "codex_bin": "/not-installed/codex",
                        "socket_path": "/run/native-codex/native.sock",
                    }
                }
            }
        )
    )
    target.chmod(0o644)
    network_created = volume_created = False

    def wait_for_catalog():
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            state = json.loads(docker("inspect", identity).stdout)[0]["State"]
            if not state["Running"]:
                log = docker("logs", identity)
                pytest.fail("Container exited: " + log.stdout + log.stderr)
            if state.get("Health", {}).get("Status") == "healthy":
                return
            time.sleep(2)
        log = docker("logs", identity)
        pytest.fail("Catalog health timeout: " + log.stdout + log.stderr)

    async def exercise(replay):
        async with httpx.AsyncClient(trust_env=False, timeout=30) as http:
            url = f"http://{gateway}:8088/mcp"
            invalid_host = await http.post(
                url,
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Host": "foreign.invalid:8088",
                },
            )
            assert invalid_host.status_code == 421
            invalid_origin = await http.post(
                url,
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Origin": "http://foreign.invalid:8088",
                },
            )
            assert invalid_origin.status_code == 403
            async with streamable_http_client(url, http_client=http) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    catalog = await session.list_tools()
                    assert len(catalog.tools) == 23
                    policy = runpy.run_path(
                        str(
                            Path(__file__).parents[1]
                            / "scripts/install_runtime_annotation_patch.py"
                        )
                    )
                    reads = policy["READ_ONLY_TOOLS"]
                    controls = policy["CONTROL_TOOLS"]
                    assert {tool.name for tool in catalog.tools} == reads | controls
                    for tool in catalog.tools:
                        assert tool.annotations is not None
                        assert tool.annotations.read_only_hint is (tool.name in reads)
                        assert tool.annotations.destructive_hint is (
                            tool.name in controls
                        )
                    schema = await http.get(f"http://{gateway}:8088/openapi.json")
                    assert schema.status_code == 200
                    assert (
                        len(
                            [
                                path
                                for path in schema.json()["paths"]
                                if path.endswith("/run")
                            ]
                        )
                        == 23
                    )
                    diagnostics = await session.call_tool(
                        "read_server_diagnostics", {"payload": {"target": "local"}}
                    )
                    assert not diagnostics.is_error
                    connection = diagnostics.structured_content["result"]["connection"]
                    assert connection["socket"] == "/run/native-codex/native.sock"
                    assert connection["server"].startswith("friday-validation-native")
                    result = await session.call_tool(
                        "create_thread_and_start_turn",
                        {
                            "payload": {
                                "target": "local",
                                "cwd": str(tmp_path),
                                "text": "Disposable container fixture only",
                                "request_id": "container-fixture-dispatch",
                                "wait_for_completion": False,
                            }
                        },
                    )
                    assert not result.is_error
                    dispatch = result.structured_content["result"]["result"]["dispatch"]
                    assert dispatch["state"] == "accepted"
                    assert dispatch["replayed"] is replay

    try:
        docker(
            "network",
            "create",
            "--subnet",
            "172.30.186.0/24",
            "--gateway",
            gateway,
            identity,
        )
        network_created = True
        docker("volume", "create", identity)
        volume_created = True
        docker(
            "run",
            "--detach",
            "--name",
            identity,
            "--network",
            "host",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--group-add",
            str(os.getgid()),
            "--tmpfs",
            "/tmp:rw,size=256m,mode=1777",
            "--mount",
            f"type=volume,src={identity},dst=/var/lib/codex-action-server",
            "--mount",
            f"type=bind,src={native_dir},dst=/run/native-codex,readonly",
            "--mount",
            f"type=bind,src={target},dst=/run/codex-action-server/targets.json,readonly",
            "--env",
            f"CODEX_ACTION_BRIDGE_GATEWAY={gateway}",
            IMAGE,
        )
        wait_for_catalog()
        asyncio.run(exercise(False))
        docker("restart", identity)
        wait_for_catalog()
        asyncio.run(exercise(True))
        assert (
            len([call for call in native.calls if call.get("method") == "turn/start"])
            == 1
        )
    finally:
        log = docker("logs", identity, check=False)
        (tmp_path / "container.log").write_text(log.stdout + log.stderr)
        native.close()
        docker("rm", "--force", identity, check=False)
        if volume_created:
            docker("volume", "rm", identity, check=False)
        if network_created:
            docker("network", "rm", identity, check=False)
