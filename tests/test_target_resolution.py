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
