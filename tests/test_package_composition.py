"""Independent compatibility contracts and opt-in disposable runtime acceptance.

Set CAS_COMPOSITION_RUNTIME=1 to run the serialized CLI/MCP/HTTP checks. The
default is lightweight verification, so ordinary pytest cannot accidentally
create several RCC environments alongside another acceptance lane.
"""

from __future__ import annotations

import ast
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads(
    (ROOT / "tests/fixtures/package_composition_contract.json").read_text()
)
OBSERVE = frozenset(
    """discover_threads get_thread_goal get_thread_snapshot inspect_target list_apps
    list_background_terminals list_hooks list_loaded_threads list_mcp_server_status
    list_models list_native_capabilities list_plugins list_skills list_targets
    list_thread_attachments list_thread_items list_thread_queue list_thread_sections
    list_thread_timeline list_thread_turns read_account_rate_limits read_account_usage
    read_app read_dispatch_receipt read_mcp_resource read_model_provider_capabilities
    read_plugin read_server_diagnostics read_thread search_thread_occurrences
    search_threads""".split()
)
CONTROL = frozenset(
    """add_thread_attachment add_thread_queue_item archive_thread call_mcp_tool
    clear_thread_goal compact_thread create_thread_and_start_turn create_thread_section
    delete_thread delete_thread_queue_item delete_thread_section fork_thread
    inject_thread_items interrupt_turn move_thread_to_section remove_thread_attachment
    reorder_thread_queue resume_thread revert_thread set_thread_goal set_thread_name
    start_review start_thread start_thread_queue start_turn steer_turn
    terminate_background_terminal unarchive_thread update_thread_metadata
    update_thread_queue_item update_thread_section update_thread_settings
    update_turn_settings""".split()
)
EXPECTED = {
    "codex-action-server": OBSERVE | CONTROL,
    "codex-observe": OBSERVE,
    "codex-control": CONTROL,
}


def _entrypoints(path):
    return {
        node.name: node
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "action"
            for decorator in node.decorator_list
        )
    }


def _assert_signatures(path, names):
    endpoints = _entrypoints(path)
    assert set(endpoints) == names
    for name, node in endpoints.items():
        fixed = CONTRACT["actions"][name]
        assert {
            arg.arg: ast.unparse(arg.annotation) for arg in node.args.args
        } == fixed["arguments"], name
        assert ast.unparse(node.returns) == fixed["return"], name


def test_fixed_baseline_has_disjoint_complete_catalog():
    assert len(OBSERVE) == 31
    assert len(CONTROL) == 33
    assert not OBSERVE & CONTROL
    assert set(CONTRACT["actions"]) == OBSERVE | CONTROL


def test_compatibility_signatures_and_payload_schemas_are_unchanged(monkeypatch):
    from actions import Response
    from test_actions import load_actions

    monkeypatch.setenv("CODEX_ACTION_PROFILE", "operator")
    monkeypatch.setenv("CODEX_ACTION_PACKAGES", "codex-action-server")
    module = load_actions()
    _assert_signatures(ROOT / "src/codex_actions.py", OBSERVE | CONTROL)
    for name, fixed in CONTRACT["actions"].items():
        output_model = eval(fixed["return"], {**vars(module), "Response": Response})
        output_schema = output_model.model_json_schema()
        effective = (
            output_schema.get("$defs", {})
            .get("EffectiveConfiguration", {})
            .get("properties", {})
        )
        for field in ("approval_policy", "sandbox_policy"):
            effective.pop(field, None)
        output_digest = hashlib.sha256(
            json.dumps(output_schema, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert output_digest == fixed["output_schema_sha256"], name
        model_name = fixed["arguments"].get("payload")
        if model_name is None:
            continue
        schema = getattr(module, model_name).model_json_schema()
        if model_name in {
            "ThreadStartRequest",
            "ThreadResumeRequest",
            "CreateThreadAndStartTurnRequest",
            "TurnStartRequest",
        }:
            for field in ("approval_policy", "sandbox"):
                schema["properties"].pop(field, None)
        digest = hashlib.sha256(
            json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert digest == fixed["payload_schema_sha256"], name


@pytest.mark.parametrize(
    "profile,packages,names",
    [
        ("operator", ("codex-action-server",), OBSERVE | CONTROL),
        ("observe", ("codex-action-server",), OBSERVE),
        ("operator", ("codex-observe",), OBSERVE),
        ("observe", ("codex-observe",), OBSERVE),
        ("operator", ("codex-control",), CONTROL),
        ("operator", ("codex-observe", "codex-control"), OBSERVE | CONTROL),
    ],
)
def test_metadata_matches_independent_deployment_catalog(profile, packages, names):
    from action_catalog_contract import action_names_for_deployment

    assert action_names_for_deployment(profile, packages) == names


@pytest.mark.parametrize(
    "selection",
    [
        "",
        " ",
        "unknown",
        "codex-observe,",
        "codex-observe,codex-observe",
        "codex-action-server,codex-observe",
        "codex-action-server,codex-control",
    ],
)
def test_package_selection_fails_closed(monkeypatch, selection):
    from action_catalog_contract import selected_package_names

    monkeypatch.setenv("CODEX_ACTION_PROFILE", "operator")
    monkeypatch.setenv("CODEX_ACTION_PACKAGES", selection)
    with pytest.raises(ValueError):
        selected_package_names()


def test_observe_profile_rejects_explicit_control():
    from action_catalog_contract import action_names_for_deployment

    for packages in (("codex-control",), ("codex-observe", "codex-control")):
        with pytest.raises(ValueError):
            action_names_for_deployment("observe", packages)


@pytest.mark.parametrize(
    "profile,selection",
    [("observe", "codex-action-server"), ("operator", "codex-observe")],
)
def test_direct_python_excluded_control_fails_before_resolution(profile, selection):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import codex_actions; from actions import ActionError; "
            "payload = codex_actions.ThreadStartRequest(target='unconfigured', cwd='/unused'); "
            "\ntry: codex_actions.start_thread(payload)\n"
            "except ActionError as error: assert 'unavailable' in str(error)\n"
            "else: raise AssertionError('excluded control was invoked')\n",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "CODEX_ACTION_PROFILE": profile,
            "CODEX_ACTION_PACKAGES": selection,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_assembled_artifacts_preserve_fixed_names_signatures_and_independence(tmp_path):
    output = tmp_path / "packages"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/assemble_packages.py"),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for package in ("codex-observe", "codex-control"):
        artifact = output / package
        _assert_signatures(artifact / "src/codex_actions.py", EXPECTED[package])
        manifest = (artifact / "package.yaml").read_text()
        assert f"name: {package}" in manifest
        assert "spec-version: v2" in manifest
        assert (artifact / "src/codex_shared/__init__.py").is_file()
        assert not any(path.is_symlink() for path in artifact.rglob("*"))
        for source in (artifact / "src/codex_shared").rglob("*.py"):
            assert not _entrypoints(source), source


def _runtime_binary():
    if os.environ.get("CAS_COMPOSITION_RUNTIME") != "1":
        pytest.skip(
            "serialized CLI composition acceptance requires CAS_COMPOSITION_RUNTIME=1"
        )
    binary = os.environ.get("ACTION_SERVER_BIN") or shutil.which("action-server")
    assert binary, "requested CLI acceptance requires installed Action Server"
    version = subprocess.run([binary, "version"], capture_output=True, text=True)
    expected_version = os.environ.get("CAS_COMPOSITION_RUNTIME_VERSION", "1.0.1")
    assert version.returncode == 0, version.stderr
    assert expected_version in version.stdout, version.stdout
    return binary


def _process_record(pid):
    try:
        stat = (Path("/proc") / str(pid) / "stat").read_text()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    fields = stat.rsplit(")", 1)[1].split()
    return fields[0], int(fields[2]), int(fields[3]), int(fields[19])


def _fixture_group_members(group):
    members = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        record = _process_record(int(entry.name))
        if record is not None:
            state, process_group, session, started = record
            if process_group == group and session == group and state != "Z":
                try:
                    owner = entry.stat().st_uid
                except (FileNotFoundError, ProcessLookupError):
                    continue
                assert owner == os.getuid(), "foreign process in fixture group"
                members[int(entry.name)] = started
    return members


def _port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
            return True
    except OSError:
        return False


def _stop_fixture_group(process, port, leader_started, *, grace_seconds=10):
    group = process.pid

    def owned_members():
        leader = _process_record(group)
        if leader is not None:
            assert leader[3] == leader_started, "fixture leader PID was reused"
        return _fixture_group_members(group)

    def signal_owned_group(signum):
        # Refresh ownership immediately before each signal. The dedicated
        # session cannot acquire unrelated members while its descendants live.
        if owned_members():
            try:
                os.killpg(group, signum)
            except ProcessLookupError:
                pass

    signal_owned_group(signal.SIGTERM)
    for duration, force in ((grace_seconds, False), (5, True)):
        if force:
            signal_owned_group(signal.SIGKILL)
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            process.poll()
            if not owned_members() and not _port_open(port):
                process.wait(timeout=1)
                return
            time.sleep(0.1)
    pytest.fail(
        f"Disposable runtime failed to release group {group}/port {port}: "
        f"live members={owned_members()}"
    )


def test_cleanup_waits_for_native_style_child_after_bootloader_parent_exits(tmp_path):
    from test_action_server_validation import _free_port

    port = _free_port()
    ready = tmp_path / "child-ready"
    child = (
        "import signal,socket,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"server=socket.socket(); server.bind(('127.0.0.1',{port})); server.listen(); "
        f"Path({str(ready)!r}).write_text('ready'); time.sleep(60)"
    )
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(60)",
        ],
        start_new_session=True,
    )
    leader_started = _process_record(parent.pid)[3]
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            assert parent.poll() is None
            time.sleep(0.05)
        assert ready.exists()
        assert _port_open(port)
        parent.terminate()
        parent.wait(timeout=5)
        assert _fixture_group_members(parent.pid), "child must outlive its bootloader"
    finally:
        _stop_fixture_group(parent, port, leader_started, grace_seconds=0.25)
    assert not _fixture_group_members(parent.pid)
    assert not _port_open(port)


@contextmanager
def _running(environment, root, phase):
    port = int(environment["CODEX_ACTION_PORT"])
    assert not _port_open(port), "previous runtime listener survived cleanup"
    log_path = root / f"server-{phase}.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            ["bash", str(ROOT / "scripts/run-packages.sh")],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    leader_started = _process_record(process.pid)[3]
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            assert process.poll() is None, log_path.read_text(errors="replace")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("Runtime readiness deadline exceeded: " + log_path.read_text())
        yield f"http://127.0.0.1:{port}"
    finally:
        _stop_fixture_group(process, port, leader_started)


@pytest.fixture
def composition_runtime(tmp_path):
    binary = _runtime_binary()
    from test_action_server_validation import NativeFixture, _free_port

    worktree = tmp_path / "worktree"
    home = tmp_path / "codex-home"
    worktree.mkdir()
    home.mkdir()
    native = NativeFixture(tmp_path / "native.sock", worktree, home)
    targets = tmp_path / "targets.json"
    targets.write_text(
        json.dumps(
            {
                "targets": {
                    "local": {
                        "transport": "local",
                        "socket_path": str(native.socket_path),
                    }
                }
            }
        )
    )
    environment = {
        **os.environ,
        "ACTION_SERVER_BIN": binary,
        "CODEX_ACTION_TARGETS": str(targets),
        "CODEX_ACTION_RECEIPTS": str(tmp_path / "receipts"),
        "CODEX_ACTION_DATA": str(tmp_path / "runtime"),
        "CODEX_ACTION_PORT": str(_free_port()),
        "CODEX_HOME": str(home),
        "CODEX_ACTION_PROFILE": "operator",
    }
    try:
        yield environment, native, tmp_path
    finally:
        native.close()


async def _exercise(
    base, packages, expected, native, *, profile="operator", dispatch=True
):
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared.exceptions import MCPError

    from test_action_server_validation import _result_data

    async with httpx.AsyncClient(trust_env=False, timeout=30) as http:
        async with streamable_http_client(f"{base}/mcp", http_client=http) as (
            read,
            write,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                catalog = await session.list_tools()
                names = [tool.name for tool in catalog.tools]
                assert len(names) == len(set(names)), "duplicate global MCP names"
                assert set(names) == expected
                tools = {tool.name: tool for tool in catalog.tools}
                openapi = await http.get(f"{base}/openapi.json")
                openapi.raise_for_status()
                routes = openapi.json()["paths"]
                selected_routes = {
                    f"/api/actions/{package}/{name.replace('_', '-')}/run": name
                    for package in packages
                    for name in EXPECTED[package] & expected
                }
                assert {
                    route for route in routes if route.startswith("/api/actions/")
                } == set(selected_routes)
                for route, name in selected_routes.items():
                    assert routes[route]["post"]["x-openai-isConsequential"] is (
                        name in CONTROL
                    ), name
                    assert routes[route]["post"]["x-operation-kind"] == "action", name
                for name, tool in tools.items():
                    schema = tool.input_schema
                    assert isinstance(schema, dict)
                    if CONTRACT["actions"][name]["arguments"]:
                        assert "payload" in schema["properties"], name
                    if os.environ.get("CAS_COMPOSITION_ANNOTATIONS") == "1":
                        assert tool.annotations.read_only_hint is (
                            name in OBSERVE
                        ), name
                        assert tool.annotations.destructive_hint is (
                            name in CONTROL
                        ), name
                        assert tool.annotations.idempotent_hint is (
                            name in OBSERVE
                        ), name
                        assert tool.annotations.open_world_hint is True, name
                    elif (
                        os.environ.get("CAS_COMPOSITION_RUNTIME_VERSION", "1.0.1")
                        == "1.0.1"
                    ):
                        # Ordinary actions in the unchanged native adapter carry
                        # conservative defaults, not the scoped wheel projection.
                        assert tool.annotations.read_only_hint is False, name
                        assert tool.annotations.destructive_hint is True, name
                        assert tool.annotations.idempotent_hint is False, name
                        assert tool.annotations.open_world_hint is True, name
                read_payload = {
                    "target": "local",
                    "cwd": native.cwd,
                    "thread_id": native.thread_id,
                    "include_turns": False,
                }
                start_payload = {
                    "target": "local",
                    "cwd": native.cwd,
                    "request_id": "composition-restart-proof",
                }
                if "list_native_capabilities" in expected:
                    inventory = _result_data(
                        await session.call_tool(
                            "list_native_capabilities", {"payload": {"target": "local"}}
                        )
                    )["result"]
                    assert inventory["selected_packages"] == list(packages)
                    assert inventory["server_exposure_profile"] == profile
                    exposed = {
                        method
                        for family in inventory["families"]
                        for method in family.get("exposed_methods", [])
                    }
                    assert "thread/read" in exposed
                    assert ("thread/start" in exposed) is ("start_thread" in expected)
                # Alternate both package keys. Equal entrypoint filenames must
                # not reuse the wrong package's imported wrapper module.
                for _ in range(2):
                    if "read_thread" in expected:
                        read_result = _result_data(
                            await session.call_tool(
                                "read_thread", {"payload": read_payload}
                            )
                        )
                        assert read_result["result"]["thread"]["id"] == native.thread_id
                    if "start_thread" in expected and dispatch:
                        started = _result_data(
                            await session.call_tool(
                                "start_thread", {"payload": start_payload}
                            )
                        )
                        assert (
                            started["result"]["dispatch"]["request_id"]
                            == start_payload["request_id"]
                        )
                if "read_thread" in expected:
                    package = (
                        "codex-action-server"
                        if "codex-action-server" in packages
                        else "codex-observe"
                    )
                    response = await http.post(
                        f"{base}/api/actions/{package}/read-thread/run",
                        json={"payload": read_payload},
                    )
                    assert response.status_code == 200, response.text
                    envelope = response.json()["result"]
                    assert envelope["operation"] == "thread/read"
                    assert envelope["result"]["thread"]["id"] == native.thread_id
                if "start_thread" in expected and dispatch:
                    package = (
                        "codex-action-server"
                        if "codex-action-server" in packages
                        else "codex-control"
                    )
                    response = await http.post(
                        f"{base}/api/actions/{package}/start-thread/run",
                        json={"payload": start_payload},
                    )
                    assert response.status_code == 200, response.text
                    envelope = response.json()["result"]
                    assert envelope["operation"] == "thread/start"
                    assert envelope["result"]["dispatch"]["replayed"] is True
                if "start_thread" not in expected:
                    before = len(native.calls)
                    try:
                        denied = await session.call_tool(
                            "start_thread", {"payload": start_payload}
                        )
                    except MCPError:
                        pass
                    else:
                        assert denied.is_error
                    for package in EXPECTED:
                        denied = await http.post(
                            f"{base}/api/actions/{package}/start-thread/run",
                            json={"payload": start_payload},
                        )
                        assert denied.status_code == 404, denied.text
                    assert (
                        len(native.calls) == before
                    ), "excluded dispatch reached native fixture"
                if "read_dispatch_receipt" in expected:
                    receipt = await session.call_tool(
                        "read_dispatch_receipt",
                        {"payload": {"request_id": start_payload["request_id"]}},
                    )
                    assert not receipt.is_error
                    assert receipt.structured_content["result"]["state"] in {
                        "created_not_materialized",
                        "not_found",
                    }
                return tools


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
def test_real_runtime_composition_catalog_http_dispatch_and_restart(
    composition_runtime, packages, profile, expected
):
    environment, native, root = composition_runtime
    environment.update(
        CODEX_ACTION_PACKAGES=",".join(packages), CODEX_ACTION_PROFILE=profile
    )
    catalogs = []
    for phase in ("first", "restart"):
        with _running(environment, root, phase) as base:
            tools = asyncio.run(
                _exercise(base, packages, expected, native, profile=profile)
            )
            catalogs.append({name: tool.input_schema for name, tool in tools.items()})
    assert catalogs[0] == catalogs[1]
    starts = [call for call in native.calls if call.get("method") == "thread/start"]
    assert len(starts) == (1 if "start_thread" in expected else 0)
    if starts:
        import dispatch_receipts

        receipt = dispatch_receipts.read(
            Path(environment["CODEX_ACTION_RECEIPTS"]), "composition-restart-proof"
        )
        assert receipt["state"] == "created_not_materialized"
        assert receipt["thread_id"] == native.thread_id


def test_real_runtime_reused_datadir_excludes_stale_aggregate_and_control(
    composition_runtime,
):
    environment, native, root = composition_runtime
    stages = [
        (("codex-action-server",), "operator", OBSERVE | CONTROL),
        (("codex-observe", "codex-control"), "operator", OBSERVE | CONTROL),
        (("codex-observe",), "observe", OBSERVE),
        (("codex-control",), "operator", CONTROL),
    ]
    schemas = {}
    for phase, (packages, profile, expected) in enumerate(stages):
        environment.update(
            CODEX_ACTION_PACKAGES=",".join(packages), CODEX_ACTION_PROFILE=profile
        )
        with _running(environment, root, f"selection-{phase}") as base:
            tools = asyncio.run(
                _exercise(base, packages, expected, native, profile=profile)
            )
            for name, tool in tools.items():
                if name in schemas:
                    assert tool.input_schema == schemas[name], name
                else:
                    schemas[name] = tool.input_schema
    assert sum(call.get("method") == "thread/start" for call in native.calls) == 1


@pytest.mark.parametrize(
    "profile,selection",
    [
        ("unknown", "codex-observe"),
        ("observe", "codex-control"),
        ("observe", "codex-observe,codex-control"),
        ("operator", "codex-action-server,codex-observe"),
    ],
)
def test_real_runtime_invalid_selection_fails_before_dispatch(
    composition_runtime, profile, selection
):
    environment, native, root = composition_runtime
    environment.update(CODEX_ACTION_PROFILE=profile, CODEX_ACTION_PACKAGES=selection)
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/run-packages.sh")],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    (root / "rejection.log").write_text(result.stdout + result.stderr)
    assert result.returncode != 0
    assert not native.calls
