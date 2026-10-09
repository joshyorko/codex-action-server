"""Generated targets stay private, live, and isolated from static routes."""

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

import boundary
from test_devsy_kubernetes_transport import pod, target_config, workspace_row
from test_target_resolution import configure


@pytest.fixture
def registry(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, {"local": {"transport": "local"}})
    path = tmp_path / "dynamic.json"
    monkeypatch.setenv("CODEX_ACTION_DYNAMIC_TARGETS", str(path))
    return path


def publish(path, targets):
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps({"version": 1, "targets": targets}))
    pending.chmod(0o600)
    pending.replace(path)


def assert_static_survives():
    assert boundary.resolve_target("local").target == "local"
    assert set(boundary.configurations()) == {"local"}
    with pytest.raises(boundary.ResolutionError, match="dynamic_target_registry_"):
        boundary.resolve_target("worker")


def test_dynamic_missing_registry_preserves_static_targets(registry):
    assert_static_survives()


def test_dynamic_disabled_does_not_discover_registry(registry, monkeypatch):
    publish(registry, {"worker": target_config()})
    monkeypatch.delenv("CODEX_ACTION_DYNAMIC_TARGETS")
    assert set(boundary.configurations()) == {"local"}
    with pytest.raises(boundary.ResolutionError, match="not configured"):
        boundary.resolve_target("worker")


def test_dynamic_targets_list_and_resolve_uid_pinned_route(registry, monkeypatch):
    publish(registry, {"worker": target_config()})
    from codex_shared.inspection import list_targets

    assert list_targets().result == {
        "targets": [
            {"name": "local", "transport": "local"},
            {"name": "worker", "transport": "devsy-kubernetes"},
        ]
    }

    def run(args, **kwargs):
        assert "exec" not in args and "start" not in args
        value = [workspace_row()] if args[0] == "devsy" else {"items": [pod()]}
        return subprocess.CompletedProcess(args, 0, json.dumps(value), "")

    monkeypatch.setattr(subprocess, "run", run)
    target = boundary.resolve_target("worker")
    assert target.logical_name == "worker"
    assert target.workspace_uid == "uid-1"
    assert target.container.pod_uid == "pod-1"


def test_dynamic_recreated_workspace_cannot_cross_uid_fence(registry, monkeypatch):
    publish(registry, {"worker": target_config()})
    row = workspace_row()
    row["uid"] = "replacement"

    def run(args, **kwargs):
        assert args[0] == "devsy", "identity mismatch must stop before Kubernetes"
        return subprocess.CompletedProcess(args, 0, json.dumps([row]), "")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(boundary.ResolutionError, match="workspace_identity_changed"):
        boundary.resolve_target("worker")


def test_dynamic_static_collision_rejects_registry_without_rebinding(registry):
    publish(registry, {"local": target_config(), "worker": target_config()})
    assert_static_survives()


@pytest.mark.parametrize(
    "content",
    [
        "secret malformed content",
        "[]",
        '{"version": true, "targets": {}}',
        '{"version": 2, "targets": {}}',
        '{"version": 1, "targets": []}',
        '{"version": 1, "targets": {}, "owners": []}',
        '{"version": 1, "targets": {}, "history": {}}',
        '{"version": 1, "targets": {}, "unknown": {}}',
    ],
)
def test_dynamic_malformed_registry_is_safe_and_isolated(registry, content):
    registry.write_text(content)
    registry.chmod(0o600)
    assert_static_survives()


@pytest.mark.parametrize(
    "override",
    [
        {"transport": "ssh"},
        {"workspace_uid": ""},
        {"workspace": ""},
        {"context": ""},
        {"provider": "docker"},
        {"user": "root"},
        {"destination": "injected"},
        {"socket_path": "/tmp/../escape"},
        {"codex_bin": "echo secret"},
    ],
)
def test_dynamic_invalid_target_fails_closed(registry, override):
    publish(registry, {"worker": {**target_config(), **override}})
    assert_static_survives()


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o666])
def test_dynamic_registry_must_be_private(registry, mode):
    publish(registry, {"worker": target_config()})
    registry.chmod(mode)
    assert_static_survives()


def test_dynamic_registry_symlink_is_rejected(registry):
    source = registry.with_suffix(".source")
    publish(source, {"worker": target_config()})
    registry.symlink_to(source)
    assert_static_survives()


def test_dynamic_registry_requires_owner(registry, monkeypatch):
    publish(registry, {"worker": target_config()})
    monkeypatch.setattr(os, "geteuid", lambda: registry.stat().st_uid + 1)
    assert_static_survives()


def test_dynamic_registry_fifo_is_rejected_without_blocking(registry):
    os.mkfifo(registry, 0o600)
    assert_static_survives()


def test_dynamic_atomic_replacement_is_seen_on_next_call(registry):
    publish(registry, {"worker": target_config()})
    assert "worker" in boundary.configurations()
    publish(registry, {"next-worker": {**target_config(), "workspace_uid": "uid-2"}})
    configs = boundary.configurations()
    assert "worker" not in configs
    assert configs["next-worker"]["workspace_uid"] == "uid-2"
    assert configs["local"] == {"transport": "local"}


def test_dynamic_provenance_metadata_does_not_change_routing(registry):
    publish(registry, {"worker": target_config()})
    data = json.loads(registry.read_text())
    data.update(
        owners={"worker": {"operation_id": "operation-1", "workspace_uid": "uid-1"}},
        history=[{"operation_id": "operation-1", "event": "registered"}],
    )
    registry.write_text(json.dumps(data))
    assert boundary.configurations()["worker"] == target_config()


def scoped_target():
    return {
        **target_config(),
        "kubernetes_context": "ror",
        "namespace": "devsy",
        "kubeconfig": "/operator/kubeconfig",
        "repository": "https://github.com/example/worker.git",
        "revision": "a" * 40,
        "recipe": ".devcontainer/devcontainer.json",
    }


@pytest.fixture
def authority(registry, monkeypatch):
    publish(registry, {"worker": scoped_target()})
    data = json.loads(registry.read_text())
    data["owners"] = {"worker": {"operation_id": "operation-1"}}
    registry.write_text(json.dumps(data))
    monkeypatch.setenv(
        "CODEX_ACTION_WORKER_AUTHORITY", "http://127.0.0.1:8089/worker-authorized"
    )
    state = {"authorized": True, "calls": []}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            assert size <= 4097
            return json.dumps({"authorized": state["authorized"]}).encode()

    class Opener:
        def open(self, url, timeout):
            assert timeout <= 3
            state["calls"].append(url)
            if state.get("unavailable"):
                raise OSError("secret-network-detail")
            return Response()

    monkeypatch.setattr(boundary, "_authority_opener", lambda: Opener())
    return state


def test_dynamic_live_authority_checked_again_after_revocation(authority):
    assert "worker" in boundary.configurations()
    query = parse_qs(urlsplit(authority["calls"][-1]).query)
    assert query == {
        "name": ["worker"],
        "uid": ["uid-1"],
        "operation_id": ["operation-1"],
    }
    authority["authorized"] = False
    assert set(boundary.configurations()) == {"local"}
    with pytest.raises(boundary.ResolutionError, match="dynamic_target_unauthorized"):
        boundary.resolve_target("worker")
    count = len(authority["calls"])
    assert boundary.resolve_target("local").target == "local"
    assert len(authority["calls"]) == count


def test_dynamic_authority_unavailable_preserves_static(authority):
    authority["unavailable"] = True
    assert set(boundary.configurations()) == {"local"}
    assert boundary.resolve_target("local").target == "local"
    with pytest.raises(
        boundary.ResolutionError, match="dynamic_target_authority_unavailable"
    ):
        boundary.resolve_target("worker")


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/worker-authorized",
        "http://example.com/worker-authorized",
        "http://8.8.8.8/worker-authorized",
        "http://user:secret@127.0.0.1/worker-authorized",
        "http://127.0.0.1/other",
        "http://127.0.0.1/worker-authorized?extra=true",
        "http://127.0.0.1/worker-authorized#fragment",
        "http://0.0.0.0/worker-authorized",
        "http://127.0.0.1:0/worker-authorized",
        "http://127.0.0.1/worker-authorized?",
        "http://127.0.0.1/worker-authorized#",
    ],
)
def test_dynamic_malformed_authority_fails_closed(authority, monkeypatch, url):
    monkeypatch.setenv("CODEX_ACTION_WORKER_AUTHORITY", url)
    assert set(boundary.configurations()) == {"local"}
    assert not authority["calls"]
    with pytest.raises(
        boundary.ResolutionError, match="dynamic_target_authority_invalid"
    ):
        boundary.resolve_target("worker")


@pytest.mark.parametrize(
    "missing",
    [
        "owners",
        "kubernetes_context",
        "namespace",
        "kubeconfig",
        "repository",
        "revision",
        "recipe",
    ],
)
def test_dynamic_authority_requires_provenance_and_bindings(
    authority, registry, missing
):
    data = json.loads(registry.read_text())
    if missing == "owners":
        del data["owners"]
    else:
        del data["targets"]["worker"][missing]
    registry.write_text(json.dumps(data))
    assert set(boundary.configurations()) == {"local"}
    assert not authority["calls"]


@pytest.mark.parametrize(
    "repository", [None, {}, 123, "https://user:secret@example.com/repo"]
)
def test_dynamic_invalid_repository_binding_preserves_static(
    authority, registry, repository
):
    data = json.loads(registry.read_text())
    data["targets"]["worker"]["repository"] = repository
    registry.write_text(json.dumps(data))
    assert set(boundary.configurations()) == {"local"}
    assert boundary.resolve_target("local").target == "local"
    assert not authority["calls"]


@pytest.mark.parametrize(
    "changed",
    [None, "cluster", "namespace", "kubeconfig", "repository", "revision", "recipe"],
)
def test_dynamic_scoped_identity_matches_actual_devsy_row(
    authority, monkeypatch, changed
):
    row = workspace_row()
    row["source"] = {
        "gitRepository": scoped_target()["repository"],
        "gitCommit": "a" * 40,
    }
    row["devContainerPath"] = scoped_target()["recipe"]
    option = {
        "cluster": "KUBERNETES_CONTEXT",
        "namespace": "KUBERNETES_NAMESPACE",
        "kubeconfig": "KUBERNETES_CONFIG",
    }
    if changed in option:
        row["provider"]["options"][option[changed]]["value"] = "changed"
    elif changed in ("repository", "revision"):
        row["source"]["gitRepository" if changed == "repository" else "gitCommit"] = (
            "changed"
        )
    elif changed == "recipe":
        row["devContainerPath"] = "changed"

    def run(args, **kwargs):
        if changed:
            assert args[0] == "devsy", "must reject before contacting changed cluster"
        value = [row] if args[0] == "devsy" else {"items": [pod()]}
        return subprocess.CompletedProcess(args, 0, json.dumps(value), "")

    monkeypatch.setattr(subprocess, "run", run)
    if changed:
        with pytest.raises(boundary.ResolutionError, match="workspace_binding_changed"):
            boundary.resolve_target("worker")
    else:
        assert boundary.resolve_target("worker").workspace_uid == "uid-1"


def test_dynamic_authority_ignores_proxy_and_refuses_http_redirect(
    registry, monkeypatch
):
    publish(registry, {"worker": scoped_target()})
    data = json.loads(registry.read_text())
    data["owners"] = {"worker": {"operation_id": "operation-1"}}
    registry.write_text(json.dumps(data))
    state = {"redirect": False, "paths": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["paths"].append(self.path)
            if state["redirect"]:
                self.send_response(302)
                self.send_header("Location", "/must-not-follow")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"authorized":true}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("no_proxy", "")
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv(
            "CODEX_ACTION_WORKER_AUTHORITY",
            f"http://127.0.0.1:{server.server_port}/worker-authorized",
        )
        assert "worker" in boundary.configurations()
        state["redirect"] = True
        assert set(boundary.configurations()) == {"local"}
        assert len(state["paths"]) == 2
        assert all(path.startswith("/worker-authorized?") for path in state["paths"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "gateway,host,expected",
    [
        ("172.30.86.1", "172.30.86.1", {"source_address": ("127.0.0.1", 0)}),
        ("172.30.86.1", "192.168.1.20", {}),
        (None, "172.30.86.1", {}),
    ],
)
def test_dynamic_authority_loopback_source_only_for_managed_gateway(
    monkeypatch, gateway, host, expected
):
    import urllib.request

    if gateway is None:
        monkeypatch.delenv("CODEX_ACTION_BRIDGE_GATEWAY", raising=False)
    else:
        monkeypatch.setenv("CODEX_ACTION_BRIDGE_GATEWAY", gateway)
    calls = []

    def do_open(self, connection, request, **kwargs):
        calls.append(kwargs)
        return "response"

    monkeypatch.setattr(urllib.request.HTTPHandler, "do_open", do_open)
    handler = next(
        item
        for item in boundary._authority_opener().handlers
        if isinstance(item, urllib.request.HTTPHandler)
    )
    request = urllib.request.Request(f"http://{host}:8089/worker-authorized")
    assert handler.http_open(request) == "response"
    assert calls == [expected]


def image_target():
    return {
        **scoped_target(),
        "source_kind": "image",
        "image_ref": "ghcr.io/joshyorko/codex-action-server",
        "image_digest": "sha256:" + "b" * 64,
    }


@pytest.mark.parametrize(
    "changed", [None, "image", "git", "cluster", "recipe_override", "image_override"]
)
def test_image_worker_binds_exact_live_source(
    authority, registry, monkeypatch, changed
):
    data = json.loads(registry.read_text())
    data["targets"]["worker"] = image_target()
    registry.write_text(json.dumps(data))
    row = workspace_row()
    row["source"] = {
        "image": image_target()["image_ref"] + "@" + image_target()["image_digest"]
    }
    row.pop("devContainerPath", None)
    if changed == "image":
        row["source"]["image"] = "ghcr.io/example/other:latest"
    elif changed == "git":
        row["source"]["gitRepository"] = "https://github.com/example/project"
    elif changed == "recipe_override":
        row["devContainerPath"] = ".devcontainer/other.json"
    elif changed == "image_override":
        row["devContainerImage"] = "other:latest"
    elif changed == "cluster":
        row["provider"]["options"]["KUBERNETES_CONTEXT"]["value"] = "other"

    def run(args, **kwargs):
        if changed:
            assert args[0] == "devsy"
        value = [row] if args[0] == "devsy" else {"items": [pod()]}
        return subprocess.CompletedProcess(args, 0, json.dumps(value), "")

    monkeypatch.setattr(subprocess, "run", run)
    if changed:
        with pytest.raises(boundary.ResolutionError, match="workspace_binding_changed"):
            boundary.resolve_target("worker")
    else:
        assert boundary.resolve_target("worker").workspace_uid == "uid-1"
        assert authority["calls"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_kind", "git"),
        ("image_ref", "https://secret@host/image"),
        ("image_digest", "sha256:bad"),
        ("image_digest", None),
        ("source_kind", None),
    ],
)
def test_image_bindings_reject_partial_or_invalid_configuration(
    authority, registry, field, value
):
    data = json.loads(registry.read_text())
    config = image_target()
    if value is None:
        config.pop(field)
    else:
        config[field] = value
    data["targets"]["worker"] = config
    registry.write_text(json.dumps(data))
    assert set(boundary.configurations()) == {"local"}
    assert not authority["calls"]
