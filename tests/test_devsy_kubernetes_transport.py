import json
import subprocess

import pytest

import boundary
from test_target_resolution import configure


def target_config():
    return {
        "transport": "devsy-kubernetes",
        "context": "default",
        "provider": "kubernetes",
        "workspace": "worker",
        "workspace_uid": "uid-1",
        "user": "vscode",
    }


def workspace_row():
    return {
        "id": "worker",
        "uid": "uid-1",
        "context": "default",
        "provider": {
            "name": "kubernetes",
            "options": {
                "KUBERNETES_CONTEXT": {"value": "ror"},
                "KUBERNETES_NAMESPACE": {"value": "devsy"},
                "KUBERNETES_CONFIG": {"value": "/operator/kubeconfig"},
            },
        },
    }


def pod(uid="pod-1", workspace_uid="uid-1"):
    return {
        "metadata": {
            "name": "devsy-uid-1",
            "namespace": "devsy",
            "uid": uid,
            "labels": {"devsy.sh/workspace-uid": workspace_uid},
        },
        "spec": {"containers": [{"name": "devsy"}]},
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {"name": "devsy", "ready": True, "state": {"running": {}}}
            ],
        },
    }


def test_pinned_kubernetes_worker_resolves_without_ssh_or_workspace_start(
    tmp_path, monkeypatch
):
    configure(tmp_path, monkeypatch, {"devsy": target_config()})
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[0] == "devsy":
            assert "list" in args
            value = [workspace_row()]
        else:
            assert args[:7] == [
                "kubectl",
                "--kubeconfig",
                "/operator/kubeconfig",
                "--context",
                "ror",
                "--namespace",
                "devsy",
            ]
            assert "get" in args and "exec" not in args
            value = {"items": [pod()]} if "pods" in args else pod()
        return subprocess.CompletedProcess(args, 0, json.dumps(value), "")

    monkeypatch.setattr(subprocess, "run", run)
    resolved = boundary.resolve_target("devsy")
    assert resolved.logical_name == "devsy"
    command = resolved.command(["app-server", "daemon", "version"])
    assert command[:7] == [
        "kubectl",
        "--kubeconfig",
        "/operator/kubeconfig",
        "--context",
        "ror",
        "--namespace",
        "devsy",
    ]
    assert command[7:14] == [
        "exec",
        "--stdin",
        "devsy-uid-1",
        "--container",
        "devsy",
        "--",
        "/bin/sh",
    ]
    assert command[14] == "-c"
    assert "DEVSY_WORKSPACE_UID" in command[15] and "DEVSY_WORKSPACE_ID" in command[15]
    assert "su" in command[15] and "vscode" in command[15]
    assert "CODEX_HOME=/home/vscode/.codex" in command[15]
    assert "app-server daemon version" in command[15]
    assert not any("ssh" in args or "up" in args or "start" in args for args in calls)


def test_explicit_kubernetes_transport_requires_uid_pin(tmp_path, monkeypatch):
    config = target_config()
    del config["workspace_uid"]
    configure(tmp_path, monkeypatch, {"devsy": config})
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: pytest.fail("unscoped probe")
    )
    with pytest.raises(ValueError, match="workspace_uid_required"):
        boundary.resolve_target("devsy")


@pytest.mark.parametrize(
    "rows", [[], [pod(), pod(uid="pod-2")], [pod(workspace_uid="other")]]
)
def test_missing_ambiguous_or_wrong_workspace_pod_never_yields_exec(
    tmp_path, monkeypatch, rows
):
    configure(tmp_path, monkeypatch, {"devsy": target_config()})

    def run(args, **kwargs):
        assert "exec" not in args
        value = [workspace_row()] if args[0] == "devsy" else {"items": rows}
        return subprocess.CompletedProcess(args, 0, json.dumps(value), "")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ValueError):
        boundary.resolve_target("devsy")


def test_replaced_pod_is_refused_before_native_command(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, {"devsy": target_config()})

    def run(args, **kwargs):
        assert "exec" not in args
        value = (
            [workspace_row()]
            if args[0] == "devsy"
            else {"items": [pod()]}
            if "pods" in args
            else pod(uid="replacement")
        )
        return subprocess.CompletedProcess(args, 0, json.dumps(value), "")

    monkeypatch.setattr(subprocess, "run", run)
    resolved = boundary.resolve_target("devsy")
    with pytest.raises(ValueError, match="workspace_pod_identity_changed"):
        resolved.command(["app-server", "daemon", "version"])


def test_stopped_pod_is_not_started(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, {"devsy": target_config()})
    stopped = pod()
    stopped["status"]["phase"] = "Pending"

    def run(args, **kwargs):
        assert "exec" not in args and "start" not in args and "up" not in args
        value = [workspace_row()] if args[0] == "devsy" else {"items": [stopped]}
        return subprocess.CompletedProcess(args, 0, json.dumps(value), "")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ValueError, match="workspace_not_running"):
        boundary.resolve_target("devsy")
