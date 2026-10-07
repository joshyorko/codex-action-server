"""Run the production image against a disposable native Unix-socket fixture."""

import asyncio
import json
import os
import subprocess
import time
import uuid

import pytest

from native_test_helpers import native_server_user_agent
from test_action_server_validation import NativeFixture
from test_thread_discovery import assert_scoped_mcp_discovery
from test_package_composition import CONTROL, EXPECTED, OBSERVE


IMAGE = os.environ.get("CODEX_ACTION_CONTAINER_IMAGE")
pytestmark = pytest.mark.skipif(
    not IMAGE,
    reason="Set CODEX_ACTION_CONTAINER_IMAGE to run the production image proof",
)


def docker(*args, check=True):
    return subprocess.run(
        ["docker", *args], text=True, capture_output=True, check=check, timeout=120
    )


class SnapshotNativeFixture(NativeFixture):
    """Add an empty, bounded 0.160.1 snapshot to the real socket fixture."""

    def _thread_state(self, include_turns):
        result = super()._thread_state(include_turns)
        result["thread"].setdefault("turns", [])
        result["thread"]["status"] = {"type": "idle"}
        return result

    def _handle(self, ws):
        fixture = self

        class SnapshotSocket:
            def recv(self):
                while True:
                    raw = ws.recv()
                    request = json.loads(raw)
                    if request.get("method") != "thread/turns/list":
                        return raw
                    with fixture._lock:
                        fixture.calls.append(request)
                    ws.send(
                        json.dumps(
                            {
                                "id": request["id"],
                                "result": {"data": [], "nextCursor": None},
                            }
                        )
                    )

            def send(self, data):
                return ws.send(data)

        super()._handle(SnapshotSocket())


@pytest.mark.parametrize(
    "packages,profile,expected",
    [
        (("codex-action-server",), "operator", OBSERVE | CONTROL),
        (("codex-action-server",), "observe", OBSERVE),
        (("codex-observe",), "operator", OBSERVE),
        (("codex-control",), "operator", CONTROL),
        (("codex-observe", "codex-control"), "operator", OBSERVE | CONTROL),
    ],
)
def test_production_image_mcp_native_socket_and_persistent_receipt(
    tmp_path, packages, profile, expected
):
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared.exceptions import MCPError

    options = json.loads(docker("info", "--format", "{{json .SecurityOptions}}").stdout)
    assert not any(
        "rootless" in option or "userns" in option for option in options
    ), "Existing host loopback/socket access requires rootful Docker without UID remapping"

    identity = "cas-fixture-" + uuid.uuid4().hex[:12]
    gateway = "172.30.186.1"
    native_dir = tmp_path / "native"
    native_dir.mkdir(mode=0o750)
    native_socket = native_dir / "native.sock"
    native = SnapshotNativeFixture(
        native_socket,
        tmp_path,
        tmp_path / "native-home",
        user_agent=native_server_user_agent(
            "0.160.1",
            originator="friday-validation-native-fixture",
            client_name="friday-validation-native-fixture",
        ),
    )
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

    def assert_baked_artifacts():
        assert docker("exec", identity, "id", "-u").stdout.strip() == "1000"
        result = docker(
            "exec",
            identity,
            "python3",
            "-c",
            "import os,runpy; from pathlib import Path; "
            "root=Path('/opt/codex-action-server'); "
            "artifacts=Path(os.environ['CODEX_ACTION_PACKAGE_ROOT']); "
            "assert artifacts==Path('/opt/codex-action-packages'); "
            "assembly=runpy.run_path(str(root/'scripts/assemble_packages.py')); "
            "assembly['verify_artifacts'](artifacts,assembly['source_fingerprint'](root)); "
            "assert all((artifacts/name/'src/codex_shared/models.py').is_file() "
            "for name in ('codex-observe','codex-control'))",
        )
        assert result.returncode == 0

    def persisted_receipt():
        result = docker(
            "exec",
            identity,
            "python3",
            "-c",
            "import json; from pathlib import Path; "
            "root=Path('/var/lib/codex-action-server/receipts'); "
            "path=root/'container-fixture-dispatch.json'; "
            "print(path.read_text() if path.exists() else 'null')",
        )
        return json.loads(result.stdout)

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
                    names = [tool.name for tool in catalog.tools]
                    assert len(names) == len(set(names))
                    assert set(names) == expected
                    for tool in catalog.tools:
                        assert tool.annotations is not None
                        assert tool.annotations.read_only_hint is (tool.name in OBSERVE)
                        assert tool.annotations.destructive_hint is (
                            tool.name in CONTROL
                        )
                        assert tool.annotations.idempotent_hint is (
                            tool.name in OBSERVE
                        )
                        assert tool.annotations.open_world_hint is True
                    schema = await http.get(f"http://{gateway}:8088/openapi.json")
                    assert schema.status_code == 200
                    routes = schema.json()["paths"]
                    expected_routes = {
                        f"/api/actions/{package}/{name.replace('_', '-')}/run": name
                        for package in packages
                        for name in EXPECTED[package] & expected
                    }
                    assert {
                        path for path in routes if path.startswith("/api/actions/")
                    } == set(expected_routes)
                    for route, name in expected_routes.items():
                        assert routes[route]["post"]["x-operation-kind"] == "action"
                        assert routes[route]["post"]["x-openai-isConsequential"] is (
                            name in CONTROL
                        )
                    if "discover_threads" in expected:
                        await assert_scoped_mcp_discovery(
                            session, {tool.name: tool for tool in catalog.tools}, native
                        )
                        diagnostics = await session.call_tool(
                            "read_server_diagnostics", {"payload": {"target": "local"}}
                        )
                        assert not diagnostics.is_error
                        connection = diagnostics.structured_content["result"][
                            "connection"
                        ]
                        assert connection["socket"] == "/run/native-codex/native.sock"
                        assert connection["server"] == native.user_agent
                        snapshot = await session.call_tool(
                            "get_thread_snapshot",
                            {
                                "payload": {
                                    "target": "local",
                                    "cwd": native.cwd,
                                    "thread_id": native.thread_id,
                                }
                            },
                        )
                        assert not snapshot.is_error
                        projected = snapshot.structured_content["result"]["result"]
                        assert projected["thread_id"] == native.thread_id
                        assert projected["cwd"] == native.cwd
                        assert projected["thread_status"] == "idle"
                        assert projected["latest_turn"] is None
                        inventory = await session.call_tool(
                            "list_native_capabilities", {"payload": {"target": "local"}}
                        )
                        assert not inventory.is_error
                        assert inventory.structured_content["result"]["result"][
                            "selected_packages"
                        ] == list(packages)
                        package = (
                            "codex-action-server"
                            if packages == ("codex-action-server",)
                            else "codex-observe"
                        )
                        response = await http.post(
                            f"http://{gateway}:8088/api/actions/{package}/read-thread/run",
                            json={
                                "payload": {
                                    "target": "local",
                                    "cwd": native.cwd,
                                    "thread_id": native.thread_id,
                                    "include_turns": False,
                                }
                            },
                        )
                        assert response.status_code == 200, response.text
                        assert (
                            response.json()["result"]["result"]["thread"]["id"]
                            == native.thread_id
                        )
                    arguments = {
                        "payload": {
                            "target": "local",
                            "cwd": str(tmp_path),
                            "text": "Disposable container fixture only",
                            "request_id": "container-fixture-dispatch",
                            "wait_for_completion": False,
                        }
                    }
                    if "create_thread_and_start_turn" in expected:
                        result = await session.call_tool(
                            "create_thread_and_start_turn", arguments
                        )
                        assert not result.is_error
                        dispatch = result.structured_content["result"]["result"][
                            "dispatch"
                        ]
                        assert dispatch["state"] == "accepted"
                        assert dispatch["replayed"] is replay
                        package = (
                            "codex-action-server"
                            if packages == ("codex-action-server",)
                            else "codex-control"
                        )
                        response = await http.post(
                            f"http://{gateway}:8088/api/actions/{package}/create-thread-and-start-turn/run",
                            json=arguments,
                        )
                        assert response.status_code == 200, response.text
                        assert (
                            response.json()["result"]["result"]["dispatch"]["replayed"]
                            is True
                        )
                    else:
                        before = len(native.calls)
                        try:
                            denied = await session.call_tool(
                                "create_thread_and_start_turn", arguments
                            )
                        except MCPError:
                            pass
                        else:
                            assert denied.is_error
                        for package in EXPECTED:
                            denied = await http.post(
                                f"http://{gateway}:8088/api/actions/{package}/create-thread-and-start-turn/run",
                                json=arguments,
                            )
                            assert denied.status_code == 404
                        assert len(native.calls) == before

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
            "--env",
            f"CODEX_ACTION_PACKAGES={','.join(packages)}",
            "--env",
            f"CODEX_ACTION_PROFILE={profile}",
            IMAGE,
        )
        wait_for_catalog()
        assert_baked_artifacts()
        asyncio.run(exercise(False))
        receipt_before = persisted_receipt()
        if "create_thread_and_start_turn" in expected:
            assert receipt_before["state"] == "accepted"
            assert receipt_before["thread_id"] == native.thread_id
            assert receipt_before["turn_id"] == native.turn_id
            assert "Disposable container fixture only" not in json.dumps(receipt_before)
        else:
            assert receipt_before is None
        docker("restart", identity)
        wait_for_catalog()
        assert_baked_artifacts()
        asyncio.run(exercise(True))
        assert persisted_receipt() == receipt_before
        assert len(
            [call for call in native.calls if call.get("method") == "turn/start"]
        ) == (1 if "create_thread_and_start_turn" in expected else 0)
    finally:
        log = docker("logs", identity, check=False)
        (tmp_path / "container.log").write_text(log.stdout + log.stderr)
        native.close()
        docker("rm", "--force", identity, check=False)
        if volume_created:
            docker("volume", "rm", identity, check=False)
        if network_created:
            docker("network", "rm", identity, check=False)
