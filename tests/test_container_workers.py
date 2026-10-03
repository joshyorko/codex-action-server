"""Container fixtures exercise both CLI dialects without a live engine."""

import json
from pathlib import Path
import subprocess

import pytest

import boundary


ROOT = Path(__file__).parents[1]
CID = "a" * 64
OTHER = "b" * 64
PREFIX = "io.codex-action-server.worker."
HOME = "/home/vscode/.codex"
BIN = "/home/vscode/.local/bin/codex"
SOURCE = "c" * 40


def record(cid=CID, running=True):
    return {
        "Id": cid,
        "Name": "/codex-worker-team-build",
        "Config": {
            "User": "vscode",
            "Labels": {
                PREFIX + "schema": "1",
                PREFIX + "owner": "team",
                PREFIX + "name": "build",
                PREFIX + "source": SOURCE,
                PREFIX + "home": HOME,
                PREFIX + "binary": BIN,
            },
        },
        "State": {"Running": running, "Paused": False, "Restarting": False},
        "HostConfig": {"PidMode": "private", "Privileged": False},
        "Mounts": [],
    }


class EngineFixture:
    def __init__(self, engine):
        self.engine = engine
        self.endpoint = "unix:///tmp/operator-engine.sock"
        self.prefix = (
            ["docker", "--host", self.endpoint]
            if engine == "docker"
            else ["podman", "--remote", "--url", self.endpoint]
        )
        self.rows = [record()]
        self.calls = []
        self.fail = None

    def run(self, args, **kwargs):
        assert args[: len(self.prefix)] == self.prefix
        command = args[len(self.prefix) :]
        self.calls.append(command)
        if self.fail and command[0] == self.fail:
            raise subprocess.CalledProcessError(1, args, stderr="secret stderr")
        if command[0] == "ps":
            out = "\n".join(r["Id"] for r in self.rows)
        elif command[:2] == ["container", "inspect"]:
            rows = [r for r in self.rows if r["Id"] == command[2]]
            if not rows:
                raise subprocess.CalledProcessError(1, args)
            out = json.dumps(rows)
        elif command[0] == "create":
            row = record(running=False)
            for index, item in enumerate(command):
                if item == "--label":
                    key, value = command[index + 1].split("=", 1)
                    row["Config"]["Labels"][key] = value
            self.rows.append(row)
            out = CID
        elif command[0] == "start":
            self.rows[0]["State"]["Running"] = True
            out = CID
        elif command[0] == "stop":
            self.rows[0]["State"]["Running"] = False
            out = CID
        elif command[0] == "rm":
            self.rows = []
            out = CID
        else:
            out = ""
        return subprocess.CompletedProcess(args, 0, out, "")


@pytest.fixture(params=["docker", "podman"])
def engine(request, monkeypatch):
    fixture = EngineFixture(request.param)
    monkeypatch.setattr(subprocess, "run", fixture.run)
    return fixture


def configured(tmp_path, monkeypatch, fixture, **overrides):
    config = {
        "transport": "container",
        "engine": fixture.engine,
        "endpoint": fixture.endpoint,
        "owner": "team",
        "worker": "build",
        **overrides,
    }
    path = tmp_path / "targets.json"
    path.write_text(json.dumps({"targets": {"build": config}}))
    monkeypatch.setenv("CODEX_ACTION_TARGETS", str(path))
    return config


def test_container_resolution_pins_immutable_route(tmp_path, monkeypatch, engine):
    configured(tmp_path, monkeypatch, engine)
    target = boundary.resolve_target("build")
    assert target.logical_name == "build"
    assert target.target == f"{engine.engine}:{CID}"
    assert target.container.container_id == CID
    assert target.container.codex_home == HOME
    assert target.codex_bin == BIN
    assert target.command(["app-server", "daemon", "version"]) == [
        *engine.prefix,
        "exec",
        "--interactive",
        "--user",
        "vscode",
        "--env",
        f"CODEX_HOME={HOME}",
        CID,
        BIN,
        "app-server",
        "daemon",
        "version",
    ]
    assert all(c[0] in {"ps", "container"} for c in engine.calls)


def test_old_resolution_never_switches_to_recreated_name(tmp_path, monkeypatch, engine):
    configured(tmp_path, monkeypatch, engine)
    before = boundary.resolve_target("build")
    engine.rows = [record(OTHER)]
    after = boundary.resolve_target("build")
    assert CID in before.command(["app-server", "proxy"])
    assert OTHER not in before.command(["app-server", "proxy"])
    assert OTHER in after.command(["app-server", "proxy"])


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("missing", "worker_not_found"),
        ("ambiguous", "worker_ambiguous"),
        ("owner", "worker_identity_mismatch"),
        ("stopped", "worker_not_running"),
        ("paused", "worker_not_running"),
        ("mount", "worker_not_isolated"),
        ("hostpid", "worker_not_isolated"),
        ("privileged", "worker_not_isolated"),
    ],
)
def test_container_resolution_fails_closed(
    tmp_path, monkeypatch, engine, mutation, error
):
    configured(tmp_path, monkeypatch, engine)
    if mutation == "missing":
        engine.rows = []
    elif mutation == "ambiguous":
        engine.rows.append(record(OTHER))
    elif mutation == "owner":
        engine.rows[0]["Config"]["Labels"][PREFIX + "owner"] = "other"
    elif mutation == "stopped":
        engine.rows[0]["State"]["Running"] = False
    elif mutation == "paused":
        engine.rows[0]["State"]["Paused"] = True
    elif mutation == "mount":
        engine.rows[0]["Mounts"] = [{"Type": "bind", "Source": "/home"}]
    elif mutation == "hostpid":
        engine.rows[0]["HostConfig"]["PidMode"] = "host"
    else:
        engine.rows[0]["HostConfig"]["Privileged"] = True
    with pytest.raises(ValueError, match=error):
        boundary.resolve_target("build")


@pytest.mark.parametrize(
    "override",
    [
        {"engine": "auto"},
        {"endpoint": "tcp://127.0.0.1:2375"},
        {"endpoint": "unix:///tmp/../engine.sock"},
        {"worker": "--all"},
        {"owner": ""},
        {"image": "arbitrary"},
        {"codex_bin": "/bin/sh"},
    ],
)
def test_container_bad_config_never_probes(tmp_path, monkeypatch, engine, override):
    configured(tmp_path, monkeypatch, engine, **override)
    with pytest.raises(ValueError):
        boundary.resolve_target("build")
    assert engine.calls == []


def test_local_only_does_not_call_any_provider(tmp_path, monkeypatch, engine):
    path = tmp_path / "targets.json"
    path.write_text('{"targets":{"local":{"transport":"local"}}}')
    monkeypatch.setenv("CODEX_ACTION_TARGETS", str(path))
    assert boundary.resolve_target("local").command(["--version"]) == [
        "codex",
        "--version",
    ]
    assert engine.calls == []


def provider(engine):
    from worker_containers import ContainerProvider

    return ContainerProvider(engine.engine, engine.endpoint, "team", "build")


def test_stop_and_delete_require_pinned_owned_container(engine):
    worker = provider(engine)
    worker.stop(CID)
    worker.delete(CID)
    assert [c for c in engine.calls if c[0] in {"stop", "rm"}] == [
        ["stop", CID],
        ["rm", "--volumes", CID],
    ]
    assert all("codex" not in c and "kill" not in c for c in engine.calls)


def test_replacement_rejected_before_stop_or_delete(engine):
    engine.rows = [record(OTHER)]
    for method in ("stop", "delete", "start"):
        with pytest.raises(ValueError, match="worker_identity_changed"):
            getattr(provider(engine), method)(CID)
    assert not any(c[0] in {"stop", "rm", "start", "exec"} for c in engine.calls)


def test_delete_requires_stopped_container(engine):
    with pytest.raises(ValueError, match="worker_must_be_stopped"):
        provider(engine).delete(CID)
    assert not any(c[0] == "rm" for c in engine.calls)


def test_failure_does_not_leak_output_or_retry(engine):
    engine.fail = "stop"
    with pytest.raises(ValueError, match="container_command_failed") as error:
        provider(engine).stop(CID)
    assert "secret" not in str(error.value)
    assert len([c for c in engine.calls if c[0] == "stop"]) == 1


def test_create_consumes_reviewed_recipe_without_host_mounts(engine, monkeypatch):
    from worker_containers import SourceRecipe

    recipe = SourceRecipe(
        ROOT,
        SOURCE,
        json.loads(
            (ROOT / ".devcontainer/remote-worker/devcontainer.json").read_text()
        ),
        b"archive",
    )
    engine.rows = []
    monkeypatch.setattr(SourceRecipe, "load", classmethod(lambda cls, path: recipe))
    result = provider(engine).create(ROOT, "https://headroom.example/v1")
    assert result.container_id == CID
    create = next(c for c in engine.calls if c[0] == "create")
    assert recipe.definition["image"] in create
    for forbidden in ["--volume", "--mount", "--privileged", "--publish", "--pid"]:
        assert forbidden not in create
    assert "CODEX_WORKER_HEADROOM_URL=https://headroom.example/v1" in create
    assert any(c[0] == "cp" and c[1] == "-" and CID in c[2] for c in engine.calls)
    hooks = [c[-1] for c in engine.calls if c[0] == "exec" and "-c" in c]
    assert recipe.definition["postCreateCommand"] in hooks
    assert recipe.definition["postStartCommand"] in hooks


def test_create_never_replays_existing_or_uncertain_creation(engine):
    with pytest.raises(ValueError, match="worker_already_exists"):
        provider(engine).create(ROOT, "https://headroom.example/v1")
    assert not any(c[0] == "create" for c in engine.calls)


def test_restart_does_not_reinstall_recipe(engine):
    engine.rows[0]["State"]["Running"] = False
    provider(engine).start(CID)
    commands = [c for c in engine.calls if c[0] == "exec"]
    assert commands
    assert all("setup.sh" not in " ".join(c) for c in commands)
    assert any("postStartCommand" in " ".join(c) for c in commands)


def test_rpc_rejects_native_home_mismatch(tmp_path, monkeypatch, engine):
    from codex_rpc import Client, RpcError

    configured(tmp_path, monkeypatch, engine)
    target = boundary.resolve_target("build")
    from dataclasses import replace

    target = replace(target, socket_path="/tmp/fixture.sock")

    class WS:
        def send(self, message):
            pass

        def close(self):
            pass

    class Proc:
        def terminate(self):
            pass

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: Proc())
    import codex_rpc

    monkeypatch.setattr(codex_rpc, "connect", lambda *a, **k: WS())
    monkeypatch.setattr(Client, "request", lambda *a, **k: {"codexHome": "/wrong/home"})
    with pytest.raises(RpcError, match="Codex home identity mismatch"):
        with Client(target):
            pytest.fail("wrong worker home connected")


def test_committed_source_excludes_untracked_secrets(tmp_path):
    import io
    import tarfile
    from worker_containers import SourceRecipe

    source = tmp_path / "source"
    source.mkdir()
    definition = source / ".devcontainer/remote-worker/devcontainer.json"
    definition.parent.mkdir(parents=True)
    definition.write_text(
        (ROOT / ".devcontainer/remote-worker/devcontainer.json").read_text()
    )
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
        check=True,
        capture_output=True,
    )
    (source / ".env").write_text("DO_NOT_COPY=fixture")
    result = SourceRecipe.load(source)
    with tarfile.open(fileobj=io.BytesIO(result.archive)) as archive:
        assert ".env" not in archive.getnames()
        assert not any(n.startswith(".git/") for n in archive.getnames())
        assert ".devcontainer/remote-worker/devcontainer.json" in archive.getnames()
    definition.write_text("{}")
    with pytest.raises(ValueError, match="clean_committed_source_required"):
        SourceRecipe.load(source)


def test_failed_setup_is_retained_and_not_blindly_replayed(engine, monkeypatch):
    from worker_containers import SourceRecipe

    recipe = SourceRecipe(
        ROOT,
        SOURCE,
        json.loads(
            (ROOT / ".devcontainer/remote-worker/devcontainer.json").read_text()
        ),
        b"archive",
    )
    monkeypatch.setattr(SourceRecipe, "load", classmethod(lambda cls, path: recipe))
    engine.rows = []
    engine.fail = "exec"
    with pytest.raises(ValueError, match="container_command_failed:exec"):
        provider(engine).create(ROOT, "http://headroom.example/v1")
    assert provider(engine).status().container_id == CID
    with pytest.raises(ValueError, match="worker_already_exists"):
        provider(engine).create(ROOT, "http://headroom.example/v1")
    assert len([c for c in engine.calls if c[0] == "create"]) == 1


def test_restart_refuses_partial_setup(engine):
    engine.fail = "exec"
    with pytest.raises(ValueError, match="worker_setup_incomplete_delete_and_recreate"):
        provider(engine).start(CID)
    assert len([c for c in engine.calls if c[0] == "exec"]) == 1


def test_ambiguous_workers_are_never_mutated(engine):
    engine.rows.append(record(OTHER))
    for method in ("start", "stop", "delete"):
        with pytest.raises(ValueError, match="worker_ambiguous"):
            getattr(provider(engine), method)(CID)
    assert all(c[0] == "ps" for c in engine.calls)


def test_cli_requires_full_identity_and_returns_missing_status(engine, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "worker_cli_fixture", ROOT / "scripts/remote/worker.py"
    )
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    args = [
        "--engine",
        engine.engine,
        "--endpoint",
        engine.endpoint,
        "--owner",
        "team",
        "--worker",
        "build",
    ]
    assert cli.main([*args, "stop", "--container-id", "short"]) == 2
    assert "invalid_container_identity" in capsys.readouterr().err
    assert engine.calls == []
    engine.rows = []
    assert cli.main([*args, "status"]) == 0
    assert json.loads(capsys.readouterr().out) is None


def test_source_content_is_pinned_to_captured_commit(tmp_path, monkeypatch):
    from worker_containers import SourceRecipe

    calls = []
    definition = (ROOT / ".devcontainer/remote-worker/devcontainer.json").read_text()

    def run(args, **kwargs):
        calls.append(args)
        operation = args[3]
        output = {
            "diff": b"",
            "rev-parse": SOURCE + "\n",
            "show": definition,
            "archive": b"archive",
        }[operation]
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(subprocess, "run", run)
    result = SourceRecipe.load(tmp_path)
    assert result.commit == SOURCE
    assert (
        next(c for c in calls if c[3] == "show")[-1]
        == SOURCE + ":.devcontainer/remote-worker/devcontainer.json"
    )
    assert next(c for c in calls if c[3] == "archive")[-1] == SOURCE


@pytest.mark.parametrize("state", ["Paused", "Restarting"])
def test_paused_or_restarting_is_not_reported_stopped(engine, state):
    engine.rows[0]["State"][state] = True
    assert provider(engine).status().running is True
    with pytest.raises(ValueError, match="worker_paused_or_restarting"):
        provider(engine).stop(CID)
    with pytest.raises(ValueError, match="worker_must_be_stopped"):
        provider(engine).delete(CID)


def test_paused_podman_false_running_is_not_deletable(engine):
    engine.rows[0]["State"].update(Running=False, Paused=True)
    with pytest.raises(ValueError, match="worker_must_be_stopped"):
        provider(engine).delete(CID)
    assert not any(c[0] == "rm" for c in engine.calls)
