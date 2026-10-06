"""Offline contract checks. These do not count as live provider acceptance."""

import importlib.util
from pathlib import Path
import socket
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_driver():
    path = ROOT / "ci" / "dagger_acceptance.py"
    assert path.is_file(), "The portable Dagger acceptance driver is missing"
    spec = importlib.util.spec_from_file_location("dagger_acceptance", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


@pytest.fixture
def source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    git(source, "config", "user.email", "acceptance@example.invalid")
    git(source, "config", "user.name", "Acceptance test")
    (source / "tracked.txt").write_text("original\n")
    git(source, "add", "tracked.txt")
    git(source, "commit", "-qm", "Source fixture")
    return source


def test_source_snapshot_preserves_real_commit_without_git_credentials(
    source, tmp_path
):
    driver = load_driver()
    git(source, "config", "http.https://example.invalid/.extraheader", "secret-fixture")
    snapshot = tmp_path / "snapshot"
    actual = driver.prepare_source(source, snapshot)
    assert actual == git(source, "rev-parse", "HEAD")
    assert git(snapshot, "rev-parse", "HEAD") == actual
    assert (snapshot / "tracked.txt").read_text() == "original\n"
    assert "secret-fixture" not in (snapshot / ".git/config").read_text()
    assert git(snapshot, "status", "--porcelain") == ""


def test_source_snapshot_rejects_uncommitted_input(source, tmp_path):
    driver = load_driver()
    (source / "tracked.txt").write_text("changed\n")
    with pytest.raises(ValueError, match="clean committed"):
        driver.prepare_source(source, tmp_path / "snapshot")


def test_socket_requires_explicit_existing_unix_socket(tmp_path):
    driver = load_driver()
    path = tmp_path / "engine.sock"
    try:
        sock = socket.socket(socket.AF_UNIX)
    except PermissionError:
        pytest.skip("This execution sandbox disallows Unix sockets")
    with sock:
        sock.bind(str(path))
        assert driver.validate_socket(str(path)) == path
    regular = tmp_path / "regular"
    regular.write_text("no")
    with pytest.raises(ValueError, match="Unix socket"):
        driver.validate_socket(str(regular))
    with pytest.raises(ValueError, match="absolute"):
        driver.validate_socket("relative.sock")


def test_cleanup_rejects_short_or_wrong_container_identity():
    driver = load_driver()
    assert driver.full_container_id({"container_id": "a" * 64}) == "a" * 64
    for value in (None, {}, {"container_id": "a" * 12}, {"container_id": "-all"}):
        with pytest.raises(ValueError, match="full container"):
            driver.full_container_id(value)


def test_workflow_runs_both_engines_without_repository_credentials():
    workflow = ROOT / ".github/workflows/provider-acceptance.yml"
    assert workflow.is_file(), "A separate real-provider workflow is missing"
    content = workflow.read_text()
    assert "engine: [docker, podman]" in content
    assert "persist-credentials: false" in content
    assert "pull_request_target" not in content
    assert "v0.21.10" in content
    assert "DAGGER_CLOUD_TOKEN" not in content


def test_cleanup_stops_running_worker_before_exact_delete(tmp_path):
    driver = load_driver()
    script = tmp_path / "scripts/remote/worker.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "import json, pathlib, sys\n"
        "root = pathlib.Path(__file__).parents[2]\n"
        "state = root / 'state.json'\n"
        "log = root / 'operations.jsonl'\n"
        "operation = sys.argv[sys.argv.index('--worker') + 2:]\n"
        "with log.open('a') as f: f.write(json.dumps(operation) + '\\n')\n"
        "worker = json.loads(state.read_text())\n"
        "if operation[0] == 'stop':\n"
        "    assert operation == ['stop', '--container-id', worker['container_id']]\n"
        "    worker['running'] = False\n"
        "elif operation[0] == 'delete':\n"
        "    assert not worker['running']\n"
        "    assert operation == ['delete', '--container-id', worker['container_id']]\n"
        "    worker = None\n"
        "state.write_text(json.dumps(worker))\n"
        "print(json.dumps(worker))\n"
    )
    import json

    identity = "a" * 64
    (tmp_path / "state.json").write_text(
        json.dumps({"container_id": identity, "running": True})
    )
    result = driver.cleanup(tmp_path, "docker", "unix:///test.sock", "test-run")
    assert result == {"status": "deleted", "container_id": identity}
    operations = [
        json.loads(line)
        for line in (tmp_path / "operations.jsonl").read_text().splitlines()
    ]
    assert operations == [
        ["status"],
        ["stop", "--container-id", identity],
        ["delete", "--container-id", identity],
        ["status"],
    ]


def test_workflow_job_env_uses_only_available_github_contexts():
    import re

    content = (ROOT / ".github/workflows/provider-acceptance.yml").read_text()
    job_env = content.split("    env:\n", 1)[1].split("    steps:\n", 1)[0]
    contexts = set(re.findall(r"\$\{\{\s*([a-zA-Z_]+)\.", job_env))
    # GitHub's contexts reference excludes runner at jobs.<job_id>.env.
    assert contexts <= {
        "github",
        "needs",
        "strategy",
        "matrix",
        "vars",
        "secrets",
        "inputs",
    }
    assert "ACCEPTANCE_OUTPUT=$RUNNER_TEMP/" in content


def test_driver_rejects_owner_longer_than_provider_limit():
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "ci/dagger_acceptance.py"),
            "--engine",
            "docker",
            "--socket",
            "/unused.sock",
            "--owner",
            "a" * 65,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "owner must be a unique run-scoped identifier" in result.stderr


def load_live_checks():
    load_driver()
    spec = importlib.util.spec_from_file_location(
        "provider_acceptance", ROOT / "ci/provider_acceptance.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_failure_diagnostics_keep_only_state_and_bounded_pid1_logs(monkeypatch):
    import json

    checks = load_live_checks()
    identity = "a" * 64
    monkeypatch.setattr(checks, "cli", lambda *a, **kw: {"container_id": identity})
    calls = []

    def engine(args, *operation, **kwargs):
        calls.append((operation, kwargs))
        if operation[:2] == ("container", "inspect"):
            return json.dumps(
                [
                    {
                        "Id": identity,
                        "State": {
                            "Status": "created",
                            "Error": "start failed",
                            "Secret": "hide",
                        },
                        "Config": {"Env": ["SECRET=do-not-export"]},
                    }
                ]
            )
        return "x" * 10000

    monkeypatch.setattr(checks, "engine", engine)
    assert hasattr(checks, "failure_diagnostics"), "Failure inspection is missing"
    result = checks.failure_diagnostics(
        type("Args", (), {"engine": "podman"})(), scope_was_absent=True
    )
    assert result == {
        "container_id": identity,
        "state": {"status": "created", "error": "start failed"},
        "pid1_logs_tail": "x" * 4096,
    }
    assert "do-not-export" not in json.dumps(result)
    assert calls == [
        (("container", "inspect", identity), {"timeout": 15}),
        (("logs", "--tail", "40", identity), {"timeout": 15}),
    ]


def test_failure_diagnostics_do_not_guess_missing_identity(monkeypatch):
    checks = load_live_checks()
    monkeypatch.setattr(checks, "cli", lambda *a, **kw: None)
    assert hasattr(checks, "failure_diagnostics"), "Failure inspection is missing"
    assert checks.failure_diagnostics(object(), scope_was_absent=True) == {
        "worker": "absent"
    }


def load_probe():
    path = ROOT / "ci/worker_probe.py"
    assert path.is_file(), "The CI-only real CLI diagnostic probe is missing"
    spec = importlib.util.spec_from_file_location("worker_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probe_redacts_credentials_and_bounds_engine_output():
    probe = load_probe()
    output = "install failed\nAuthorization: Bearer do-not-export\nAPI_KEY=hidden\n"
    output += "device code: ABCD-EFGH\n"
    cleaned = probe.redact_output(output)
    assert len(probe.redact_output("x" * 8000)) == 1600
    assert "install failed" in cleaned
    assert "do-not-export" not in cleaned
    assert "hidden" not in cleaned
    assert "ABCD-EFGH" not in cleaned


def test_probe_preserves_engine_error_and_surfaces_hook_stage(capsys):
    import json
    from worker_containers import WorkerError

    probe = load_probe()
    native_error = WorkerError("container_command_failed:exec")

    def failing_engine(self, args, **kwargs):
        try:
            raise subprocess.CalledProcessError(
                1, ["podman", "exec"], output="installing", stderr="brew install failed"
            )
        except subprocess.CalledProcessError:
            raise native_error from None

    traced = probe.trace_engine_run(failing_engine)
    with pytest.raises(WorkerError) as caught:
        traced(
            object(),
            ["exec", "id", "/bin/sh", "-c", "/bin/bash scripts/remote/setup.sh"],
        )
    assert caught.value is native_error
    diagnostic = json.loads(capsys.readouterr().err.strip())
    assert diagnostic["stage"] == "recipe_create_hook"
    assert diagnostic["operation"] == "exec"
    assert diagnostic["returncode"] == 1
    assert diagnostic["stderr"] == "brew install failed"
    assert diagnostic["stdout"] == "installing"


def test_failure_diagnostics_skip_unproven_preexisting_scope(monkeypatch):
    checks = load_live_checks()

    def forbidden_status(*args, **kwargs):
        pytest.fail("An unproven scope must not be inspected")

    monkeypatch.setattr(checks, "cli", forbidden_status)
    assert checks.failure_diagnostics(object(), scope_was_absent=False) == {
        "worker": "scope_not_proven_empty"
    }


def test_driver_refuses_preexisting_scope_without_cleanup(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import json

    driver = load_driver()
    report = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["driver", "--engine", "docker", "--socket", "/s", "--output", str(report)],
    )
    monkeypatch.setattr(driver, "validate_socket", lambda value: Path(value))
    monkeypatch.setattr(driver, "prepare_source", lambda *args: "a" * 40)
    monkeypatch.setattr(
        driver, "worker_status", lambda *args: {"container_id": "b" * 64}, raising=False
    )
    canary = SimpleNamespace(
        poll=lambda: None, terminate=lambda: None, wait=lambda **kw: None
    )
    monkeypatch.setattr(driver.subprocess, "Popen", lambda *a, **kw: canary)
    cleanup_calls = []
    monkeypatch.setattr(driver, "cleanup", lambda *args: cleanup_calls.append(args))

    async def unexpected_acceptance(*args):
        pytest.fail("Existing worker must be refused before Dagger")

    monkeypatch.setattr(driver, "acceptance", unexpected_acceptance)
    assert driver.main() == 1
    assert cleanup_calls == []
    assert "already has a worker" in json.loads(report.read_text())["error"]


def test_worker_exec_uses_source_cwd_instead_of_pid1_cwd(monkeypatch):
    from worker_containers import ROOT as worker_root

    checks = load_live_checks()
    monkeypatch.setattr(checks, "engine", lambda args, *command: command)
    result = checks.worker_exec(
        object(),
        {"container_id": "a" * 64, "codex_home": "/home/vscode/.codex"},
        "test",
        "-f",
        "scripts/remote/setup.sh",
    )
    assert result[result.index("--workdir") + 1] == worker_root


@pytest.mark.parametrize(
    (
        "user_agent",
        "diagnostics_result",
        "expected_diagnostics_status",
        "expected_methods",
        "expected_error",
    ),
    [
        (
            "codex-cli 0.160.1",
            {"process": {"id": 1}, "gauges": []},
            "passed",
            ["thread/list", "server/diagnostics"],
            None,
        ),
        (
            "codex-cli 0.159.2",
            None,
            "skipped",
            ["thread/list"],
            None,
        ),
        (
            "codex-cli 0.160.1",
            "rpc-error",
            "failed",
            ["thread/list", "server/diagnostics"],
            "RpcError",
        ),
        (
            "codex-cli 0.160.1",
            {"process": {}, "gauges": []},
            "failed",
            ["thread/list", "server/diagnostics"],
            "invalid_response",
        ),
    ],
)
def test_provider_smoke_gates_diagnostics_by_exact_native_version(
    monkeypatch,
    tmp_path,
    user_agent,
    diagnostics_result,
    expected_diagnostics_status,
    expected_methods,
    expected_error,
):
    from types import SimpleNamespace
    import codex_rpc

    checks = load_live_checks()
    calls = []
    # smoke() sets its process-wide target file as the real driver does. Keep
    # that mutation within this test instead of leaking it to the suite.
    monkeypatch.setenv("CODEX_ACTION_TARGETS", "restore-after-smoke-test")

    class NativeClient:
        metadata = {
            "codexHome": "/home/vscode/.codex",
            "userAgent": user_agent,
        }

        def __init__(self, target, timeout):
            self.target = target
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, method, params):
            calls.append((method, params))
            if method == "server/diagnostics":
                if diagnostics_result == "rpc-error":
                    raise codex_rpc.RpcError("private native error detail")
                return diagnostics_result
            if method == "thread/list":
                return {"data": []}
            pytest.fail(f"Unexpected native method in read-only smoke: {method}")

    target = SimpleNamespace(native=True)
    monkeypatch.setattr(codex_rpc, "Client", NativeClient)
    monkeypatch.setattr("boundary.resolve_target", lambda name: target)

    _, evidence = checks.smoke(
        SimpleNamespace(engine="docker", endpoint="unix:///test.sock", owner="test"),
        {"codex_home": "/home/vscode/.codex"},
        tmp_path / "targets.json",
    )

    assert [method for method, _params in calls] == expected_methods
    assert evidence == {
        "codex_home": "/home/vscode/.codex",
        "native_user_agent": user_agent,
        "diagnostics_status": expected_diagnostics_status,
        **({"diagnostics_error": expected_error} if expected_error is not None else {}),
        **(
            {"diagnostics_skip_reason": "native_user_agent_not_pinned"}
            if expected_diagnostics_status == "skipped"
            else {}
        ),
        "thread_list_read": True,
        "discovered_threads": 0,
    }


def test_run_records_codex_version_before_pinned_diagnostics_failure(
    monkeypatch, tmp_path
):
    import json
    from types import SimpleNamespace

    checks = load_live_checks()
    recipe = json.loads(
        (checks.ROOT / ".devcontainer/remote-worker/devcontainer.json").read_text()
    )
    worker = {
        "container_id": "a" * 64,
        "codex_bin": "/home/vscode/.local/bin/codex",
        "codex_home": "/home/vscode/.codex",
        "source_commit": "fixture-source-sha",
    }
    native_evidence = {
        "native_user_agent": "codex-cli 0.160.1",
        "diagnostics_status": "failed",
        "diagnostics_error": "RpcError",
    }
    worker_commands = []

    def fake_cli(_args, *operation, **_kwargs):
        if operation == ("status",):
            return None
        if operation[0] == "create":
            return worker
        pytest.fail(f"Unexpected provider CLI operation: {operation}")

    def fake_worker_exec(_args, _worker, *operation):
        worker_commands.append(operation)
        return "codex-cli 0.160.1"

    monkeypatch.setattr(
        checks, "command", lambda *_args, **_kwargs: "fixture-source-sha"
    )
    monkeypatch.setattr(checks, "engine", lambda *_args, **_kwargs: "engine-version")
    monkeypatch.setattr(checks, "cli", fake_cli)
    monkeypatch.setattr(
        checks,
        "check_isolation",
        lambda *_args, **_kwargs: {
            "image_reference": recipe["image"],
            "image_id": "sha256:" + "b" * 64,
            "mount_count": 0,
            "privileged": False,
        },
    )
    monkeypatch.setattr(checks, "worker_exec", fake_worker_exec)
    monkeypatch.setattr(
        checks,
        "smoke",
        lambda *_args, **_kwargs: (object(), native_evidence),
    )
    report = {}

    with pytest.raises(RuntimeError, match="Pinned native diagnostics failed"):
        checks.run(
            SimpleNamespace(
                engine="docker", endpoint="unix:///test.sock", owner="test"
            ),
            report,
        )

    assert report["first_rpc"] == native_evidence
    assert report["installed_versions"]["codex"] == "codex-cli 0.160.1"
    assert worker_commands == [("/home/vscode/.local/bin/codex", "--version")]


@pytest.mark.parametrize("executable", ["mkdir", "chown", "touch", "test"])
def test_probe_names_fixed_worker_preparation_commands(executable):
    probe = load_probe()
    assert (
        probe.command_stage(["exec", "--user", "root", "a" * 64, executable])
        == "worker_" + executable
    )


def test_read_only_transport_probe_uses_fixed_commands_and_full_id(monkeypatch):
    from types import SimpleNamespace
    from worker_containers import ROOT as worker_root

    probe = load_probe()
    calls = []

    def completed(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(probe.subprocess, "run", completed)
    assert hasattr(
        probe, "read_only_transport_probe"
    ), "Transport comparison is missing"
    identity = "a" * 64
    result = probe.read_only_transport_probe(
        "podman", "unix:///explicit.sock", identity
    )
    assert result["source_directory"]["returncode"] == 0
    assert result["exec_true"]["returncode"] == 0
    assert [call[0] for call in calls] == [
        [
            "podman",
            "--remote",
            "--url",
            "unix:///explicit.sock",
            "cp",
            identity + ":" + worker_root,
            "-",
        ],
        [
            "podman",
            "--remote",
            "--url",
            "unix:///explicit.sock",
            "exec",
            "--user",
            "root",
            "--workdir",
            "/",
            identity,
            "/bin/true",
        ],
    ]
    assert all(call[1]["timeout"] == 10 for call in calls)
    assert all(call[1]["stdout"] == subprocess.DEVNULL for call in calls)


def test_read_only_transport_probe_records_timeout_without_retry(monkeypatch):
    probe = load_probe()
    calls = []

    def timed_out(args, **kwargs):
        calls.append(args)
        raise subprocess.TimeoutExpired(args, 10, stderr=b"device code: ABCD-EFGH")

    monkeypatch.setattr(probe.subprocess, "run", timed_out)
    assert hasattr(
        probe, "read_only_transport_probe"
    ), "Transport comparison is missing"
    result = probe.read_only_transport_probe(
        "podman", "unix:///explicit.sock", "a" * 64
    )
    assert len(calls) == 2
    assert result["exec_true"]["timeout_seconds"] == 10
    assert "ABCD-EFGH" not in result["exec_true"]["stderr"]


def test_runner_uses_verified_matching_static_podman_without_changing_base():
    driver = load_driver()
    assert driver.PODMAN_REMOTE_VERSION == "4.9.3"
    assert driver.PODMAN_REMOTE_SHA256 == {
        "amd64": "b21cad103bda0c71648e424b40730bb59e668fc5bb98ed17209c9b1c93880991",
        "arm64": "a432625b0a697ddcd7a74044d2a5e1ff28026df1985bafe15c30194051c157f5",
    }
    source = (ROOT / "ci/dagger_acceptance.py").read_text()
    assert '.from_("python:3.12-slim-bookworm")' in source
    assert "sha256sum -c -" in source
    assert "client.http(" in source
    assert "/usr/local/bin/podman" in source


def test_controller_cleanup_uses_exact_id_after_failure(monkeypatch, tmp_path):
    from types import SimpleNamespace

    driver = load_driver()
    assert hasattr(driver, "run_host_controller"), "Direct-socket controller is missing"
    identity = "b" * 64
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        operation = args[3]
        if operation == "load":
            return "Loaded image ID: sha256:" + "a" * 64
        if operation == "create":
            return identity
        if operation == "start":
            raise RuntimeError("controller start failed")
        if operation == "rm":
            return identity
        raise AssertionError(args)

    monkeypatch.setattr(driver, "command", command)
    args = SimpleNamespace(
        socket="/tmp/podman.sock", controller_socket="/tmp/docker.sock"
    )
    with pytest.raises(RuntimeError, match="controller start failed"):
        driver.run_host_controller(tmp_path / "image.tar", args, "owner", tmp_path)
    assert calls[-1] == [
        "docker",
        "--host",
        "unix:///tmp/docker.sock",
        "rm",
        "--force",
        identity,
    ]
    created = next(call for call in calls if call[3] == "create")
    assert created.count("--mount") == 1
    assert "type=bind,source=/tmp/podman.sock,target=/run/provider.sock" in created
    assert "--privileged" not in created


def test_controller_returns_same_full_acceptance_report(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace

    driver = load_driver()
    assert hasattr(driver, "run_host_controller"), "Direct-socket controller is missing"
    identity = "c" * 64
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        operation = args[3]
        if operation == "load":
            return "Loaded image ID: sha256:" + "a" * 64
        if operation == "create":
            return identity
        if operation == "wait":
            return "0"
        if operation == "cp":
            assert args[4] == identity + ":/evidence/result.json"
            Path(args[5]).write_text(
                json.dumps({"status": "passed", "stage": "complete"})
            )
        return ""

    monkeypatch.setattr(driver, "command", command)
    args = SimpleNamespace(
        socket="/tmp/podman.sock", controller_socket="/tmp/docker.sock"
    )
    result = driver.run_host_controller(tmp_path / "image.tar", args, "owner", tmp_path)
    assert result["status"] == "passed"
    assert result["controller_id"] == identity
    assert result["test_transport"] == "direct_bind_socket"
    assert calls[-1][-3:] == ["rm", "--force", identity]


def test_recipe_image_identity_allows_podman_tag_normalization():
    checks = load_live_checks()
    assert hasattr(checks, "image_identity"), "Digest-aware image comparison is missing"
    digest = "sha256:" + "a" * 64
    expected = "registry.example:5000/team/worker:secure@" + digest
    actual = "registry.example:5000/team/worker@" + digest
    assert checks.image_identity(expected) == checks.image_identity(actual)
    assert checks.image_identity(expected) != checks.image_identity(
        "other/worker@" + digest
    )
    assert checks.image_identity(expected) != checks.image_identity(
        "registry.example:5000/team/worker@sha256:" + "b" * 64
    )
    with pytest.raises(ValueError):
        checks.image_identity("registry.example:5000/team/worker:secure")
