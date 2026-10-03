import json
import sys
from pathlib import Path
import subprocess

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import boundary


def configure(tmp_path, monkeypatch, targets):
    path = tmp_path / "targets.json"
    path.write_text(json.dumps({"targets": targets}))
    monkeypatch.setenv("CODEX_ACTION_TARGETS", str(path))


def test_operator_local_socket_is_resolved(tmp_path, monkeypatch):
    configure(
        tmp_path,
        monkeypatch,
        {"local": {"transport": "local", "socket_path": "/tmp/isolated.sock"}},
    )
    assert boundary.resolve_target("local").socket_path == "/tmp/isolated.sock"


def test_devsy_uses_authoritative_json_then_generated_ssh_config(tmp_path, monkeypatch):
    configure(
        tmp_path,
        monkeypatch,
        {
            "devsy": {
                "transport": "devsy",
                "context": "default",
                "workspace": "dynamic-work",
                "user": "vscode",
            }
        },
    )
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if "list" in args:
            out = json.dumps(
                [
                    {
                        "id": "dynamic-work",
                        "uid": "uid-1",
                        "context": "default",
                        "provider": {"name": "kubernetes"},
                    }
                ]
            )
        elif "status" in args:
            out = json.dumps(
                {"id": "dynamic-work", "context": "default", "state": "Running"}
            )
        else:
            out = "hostname 127.0.0.1\nport 10801\nuser vscode\n"
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(subprocess, "run", run)
    target = boundary.resolve_target("devsy")
    assert target.target == "dynamic-work.devsy"
    assert target.logical_name == "devsy"
    assert any("list" in c and "--result-format" in c for c in calls)
    assert any("status" in c for c in calls)
    assert calls[-1][-1] == "dynamic-work.devsy"


@pytest.mark.parametrize(
    "name", ["-oProxyCommand=touch /tmp/pwn", "host;id", "unknown", "../local"]
)
def test_unknown_target_never_runs_process(name, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: pytest.fail("unexpected process")
    )
    with pytest.raises(ValueError):
        boundary.resolve_target(name)


def test_missing_devsy_workspace_fails_closed(tmp_path, monkeypatch):
    configure(
        tmp_path,
        monkeypatch,
        {"devsy": {"transport": "devsy", "workspace": "missing", "context": "default"}},
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(args, 0, "[]", ""),
    )
    with pytest.raises(ValueError, match="workspace_not_found"):
        boundary.resolve_target("devsy")


@pytest.mark.parametrize(
    "output,code",
    [
        (
            "hostname dynamic-work.devsy\nport 22\nuser vscode\n",
            "devsy_active_tcp_route_required",
        ),
        ("hostname 127.0.0.1\nport 10801\nuser root\n", "ssh_user_mismatch"),
        (
            "hostname 127.0.0.1\nport 10801\nuser vscode\nproxycommand devsy workspace ssh\n",
            "devsy_active_tcp_route_required",
        ),
    ],
)
def test_generated_route_must_be_existing_tcp_not_lifecycle_proxy(
    tmp_path, monkeypatch, output, code
):
    configure(
        tmp_path,
        monkeypatch,
        {"devsy": {"transport": "devsy", "workspace": "dynamic-work"}},
    )

    def run(args, **kw):
        value = (
            json.dumps([{"id": "dynamic-work", "context": "default"}])
            if "list" in args
            else json.dumps(
                {"id": "dynamic-work", "context": "default", "state": "Running"}
            )
            if "status" in args
            else output
        )
        return subprocess.CompletedProcess(args, 0, value, "")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ValueError, match=code):
        boundary.resolve_target("devsy")


@pytest.mark.parametrize(
    "count,code", [(0, "workspace_not_found"), (2, "workspace_ambiguous")]
)
def test_source_discovery_never_selects_latest(tmp_path, monkeypatch, count, code):
    configure(
        tmp_path,
        monkeypatch,
        {
            "devsy": {
                "transport": "devsy",
                "source": "https://github.com/example/repo.git",
            }
        },
    )
    rows = [
        {
            "id": f"workspace-{n}",
            "context": "default",
            "source": {"gitRepository": "https://github.com/example/repo.git"},
        }
        for n in range(count)
    ]
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(args, 0, json.dumps(rows), ""),
    )
    with pytest.raises(ValueError, match=code):
        boundary.resolve_target("devsy")


def test_native_command_quotes_fixed_remote_arguments_without_local_shell():
    from codex_rpc import Target
    import shlex

    command = Target("allowed-host", "/opt/my codex").command(
        ["app-server", "proxy", "--sock", "/tmp/socket;touch /tmp/pwn"]
    )
    assert command[:2] == ["ssh", "-T"]
    assert command[-2] == "allowed-host"
    assert shlex.split(command[-1]) == [
        "/opt/my codex",
        "app-server",
        "proxy",
        "--sock",
        "/tmp/socket;touch /tmp/pwn",
    ]


def test_devsy_probe_errors_do_not_leak_stderr(tmp_path, monkeypatch):
    configure(
        tmp_path, monkeypatch, {"devsy": {"transport": "devsy", "workspace": "example"}}
    )

    def run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr="secret-token")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ValueError) as caught:
        boundary.resolve_target("devsy")
    assert "secret" not in str(caught.value)


def test_wrong_context_same_name_cannot_reach_native_command(tmp_path):
    from codex_rpc import Target
    import os

    # The same SSH alias may map elsewhere; the fixed remote command checks UID
    # on that actual connection before executing Codex, for probe AND proxy.
    target = Target(
        "same.devsy",
        "/bin/echo",
        None,
        "devsy",
        ("-p", "10801"),
        "selected-uid",
        "same",
    )
    for args in [
        ["app-server", "daemon", "version"],
        ["app-server", "proxy", "--sock", "/tmp/x"],
    ]:
        command = target.command(args)
        result = subprocess.run(
            ["sh", "-c", command[-1]],
            env={
                **os.environ,
                "DEVSY_WORKSPACE_UID": "other-context-uid",
                "DEVSY_WORKSPACE_ID": "same",
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert result.stdout == ""
        good = subprocess.run(
            ["sh", "-c", command[-1]],
            env={
                **os.environ,
                "DEVSY_WORKSPACE_UID": "selected-uid",
                "DEVSY_WORKSPACE_ID": "same",
            },
            capture_output=True,
            text=True,
        )
        assert good.returncode == 0
        assert "app-server" in good.stdout
