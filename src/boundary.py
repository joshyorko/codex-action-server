"""Operator-owned logical targets. No lifecycle or caller-selected transport."""

from __future__ import annotations

from dataclasses import dataclass
from http.client import HTTPConnection
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from codex_rpc import Target
from worker_containers import ContainerProvider
from worker_kubernetes import KubernetesWorker

_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_IDENTIFIER = re.compile(r"^[^\s\r\n]+$")
_WORKSPACE_BINDINGS = {
    "kubernetes_context",
    "namespace",
    "kubeconfig",
    "repository",
    "revision",
    "recipe",
}


class ResolutionError(ValueError):
    """Stable safe code, without subprocess output or operator config values."""


@dataclass(frozen=True)
class TargetSpec:
    name: str
    destination: str
    codex_bin: str = "codex"
    socket_path: str | None = None

    def rpc_target(self):
        return Target(self.destination, self.codex_bin, self.socket_path, self.name)


# Safe out-of-box behavior: only the local daemon, never guessed remote hosts.
TARGETS = {"local": TargetSpec("local", "local")}


def _name(value):
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ResolutionError("invalid_target_configuration")
    return value


def _static_configurations():
    file = os.environ.get("CODEX_ACTION_TARGETS")
    if not file:
        return {
            name: {
                "transport": "local",
                "codex_bin": spec.codex_bin,
                "socket_path": spec.socket_path,
            }
            for name, spec in TARGETS.items()
        }
    try:
        data = json.loads(Path(file).read_text())
        if (
            set(data) != {"targets"}
            or not isinstance(data["targets"], dict)
            or not data["targets"]
        ):
            raise ValueError()
        for name, config in data["targets"].items():
            _name(name)
            if not isinstance(config, dict):
                raise ValueError()
        return data["targets"]
    except (OSError, ValueError, TypeError):
        raise ResolutionError("invalid_target_configuration") from None


class _NoAuthorityRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _AuthorityHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, request):
        # Host-network CAS must retain loopback identity when contacting its
        # managed bridge; unrelated private authorities retain normal routing.
        options = {}
        gateway = os.environ.get("CODEX_ACTION_BRIDGE_GATEWAY")
        if gateway and urllib.parse.urlsplit(request.full_url).hostname == gateway:
            options["source_address"] = ("127.0.0.1", 0)
        return self.do_open(HTTPConnection, request, **options)


def _authority_opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _AuthorityHTTPHandler(), _NoAuthorityRedirect()
    )


def _authority_url():
    raw = os.environ.get("CODEX_ACTION_WORKER_AUTHORITY")
    if not raw:
        return None
    try:
        url = urllib.parse.urlsplit(raw)
        host = url.hostname
        private = host == "localhost"
        if not private:
            address = ipaddress.IPv4Address(host)
            private = address.is_loopback or any(
                address in ipaddress.IPv4Network(network)
                for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
            )
        if (
            not private
            or url.scheme != "http"
            or url.username is not None
            or url.password is not None
            or url.path != "/worker-authorized"
            or "?" in raw
            or "#" in raw
            or not 1 <= (80 if url.port is None else url.port) <= 65535
            or any(c.isspace() or ord(c) < 32 for c in raw)
        ):
            raise ValueError()
        return raw
    except (ValueError, TypeError):
        raise ResolutionError("dynamic_target_authority_invalid") from None


def _authorize_worker(url, config, owner):
    if not isinstance(owner, dict):
        raise ResolutionError("dynamic_target_registry_invalid")
    operation = owner.get("operation_id")
    _name(operation)
    query = urllib.parse.urlencode(
        {
            "name": config["workspace"],
            "uid": config["workspace_uid"],
            "operation_id": operation,
        }
    )
    try:
        with _authority_opener().open(url + "?" + query, timeout=3) as response:
            raw = response.read(4097)
        if len(raw) > 4096:
            raise ValueError()
        result = json.loads(raw)
        if not isinstance(result, dict) or type(result.get("authorized")) is not bool:
            raise ValueError()
    except (OSError, ValueError, TypeError, RecursionError, urllib.error.URLError):
        raise ResolutionError("dynamic_target_authority_unavailable") from None
    if not result["authorized"]:
        raise ResolutionError("dynamic_target_unauthorized")


def _validate_workspace_bindings(config):
    if not _WORKSPACE_BINDINGS & set(config):
        return
    if not _WORKSPACE_BINDINGS <= set(config):
        raise ValueError()
    for field in ("kubernetes_context", "namespace"):
        _name(config[field])
    validate_cwd(config["kubeconfig"])
    if not isinstance(config["repository"], str):
        raise ValueError()
    repository = urllib.parse.urlsplit(config["repository"])
    if (
        repository.scheme != "https"
        or not repository.hostname
        or repository.username is not None
        or repository.password is not None
        or repository.query
        or repository.fragment
        or not re.fullmatch(r"[a-fA-F0-9]{40}", config["revision"])
    ):
        raise ValueError()
    recipe = config["recipe"]
    if (
        not isinstance(recipe, str)
        or not recipe
        or recipe.startswith("/")
        or ".." in PurePosixPath(recipe).parts
        or "\\" in recipe
        or any(c.isspace() or ord(c) < 32 for c in recipe)
    ):
        raise ValueError()


def _dynamic_configurations(static, requested_name=None):
    file = os.environ.get("CODEX_ACTION_DYNAMIC_TARGETS")
    if not file:
        return {}
    try:
        # Open once: atomic replacement cannot swap the checked file underneath
        # this read. NONBLOCK also prevents a configured FIFO from hanging calls.
        descriptor = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "r") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
                or info.st_size > 2 * 1024 * 1024
            ):
                raise ValueError()
            raw = stream.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError()
            data = json.loads(raw)
        if (
            not isinstance(data, dict)
            or not {"version", "targets"}
            <= set(data)
            <= {"version", "targets", "owners", "history"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or not isinstance(data["targets"], dict)
            or not isinstance(data.get("owners", {}), dict)
            or not isinstance(data.get("history", []), list)
        ):
            raise ValueError()
        authority = _authority_url()
        required = {"transport", "context", "workspace", "workspace_uid", "provider"}
        allowed = required | {"codex_bin", "socket_path", "user"} | _WORKSPACE_BINDINGS
        if authority:
            required |= _WORKSPACE_BINDINGS
        for name, config in data["targets"].items():
            _name(name)
            if name in static:
                raise ResolutionError("dynamic_target_registry_collision")
            if (
                not isinstance(config, dict)
                or not required <= set(config) <= allowed
                or config["transport"] != "devsy-kubernetes"
                or config["provider"] != "kubernetes"
                or config.get("user", "vscode") != "vscode"
            ):
                raise ValueError()
            for field in ("context", "workspace", "workspace_uid"):
                _name(config[field])
            if "socket_path" in config:
                validate_cwd(config["socket_path"])
            if "codex_bin" in config:
                binary = validate_cwd(config["codex_bin"])
                if any(c.isspace() for c in binary):
                    raise ValueError()
            _validate_workspace_bindings(config)
        if not authority:
            return data["targets"]
        authorized = {}
        for name, config in data["targets"].items():
            if requested_name is not None and name != requested_name:
                continue
            try:
                _authorize_worker(authority, config, data.get("owners", {}).get(name))
            except ResolutionError:
                if requested_name is not None:
                    raise
                continue
            authorized[name] = config
        return authorized
    except ResolutionError as error:
        if str(error).startswith("dynamic_target_"):
            raise
        raise ResolutionError("dynamic_target_registry_invalid") from None
    except OSError:
        raise ResolutionError("dynamic_target_registry_unavailable") from None
    except (ValueError, TypeError, RecursionError):
        raise ResolutionError("dynamic_target_registry_invalid") from None


def _configuration_snapshot(requested_name=None):
    static = _static_configurations()
    if isinstance(requested_name, str) and requested_name in static:
        return static, None
    try:
        return {**static, **_dynamic_configurations(static, requested_name)}, None
    except ResolutionError as error:
        # A broken generated registry must never take existing workers offline.
        return static, error


def configurations():
    """List valid targets; a rejected dynamic registry never masks static routes."""
    return _configuration_snapshot()[0]


def _run(args, json_output=False):
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=15, check=True
        )
        if len(result.stdout) > 2 * 1024 * 1024:
            raise ResolutionError("resolution_output_too_large")
        return json.loads(result.stdout) if json_output else result.stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        raise ResolutionError("target_resolution_probe_failed") from None


def _devsy_workspace(config):
    context = _name(config.get("context", "default"))
    workspace = config.get("workspace")
    source = config.get("source")
    if bool(workspace) == bool(source):
        raise ResolutionError("configure_workspace_or_source_selector")
    if workspace:
        _name(workspace)
    elif (
        not isinstance(source, str)
        or not source.startswith("https://")
        or "@" in source
    ):
        raise ResolutionError("invalid_source_selector")
    # Devsy v1.19.0 cmd/workspace/list.go emits Workspace[], not an invented API.
    prefix = ["devsy", "--context", context, "--result-format", "json", "workspace"]
    rows = _run([*prefix, "list", "--skip-pro"], True)
    if not isinstance(rows, list):
        raise ResolutionError("invalid_devsy_response")
    matches = [
        r
        for r in rows
        if isinstance(r, dict)
        and r.get("context") == context
        and (
            r.get("id") == workspace
            if workspace
            else r.get("source", {}).get("gitRepository") == source
        )
        and (
            not config.get("provider")
            or r.get("provider", {}).get("name") == config["provider"]
        )
    ]
    if len(matches) != 1:
        raise ResolutionError(
            "workspace_not_found" if not matches else "workspace_ambiguous"
        )
    row = matches[0]
    workspace = _name(row.get("id"))
    if config.get("workspace_uid") and row.get("uid") != config["workspace_uid"]:
        raise ResolutionError("workspace_identity_changed")
    if _WORKSPACE_BINDINGS & set(config):
        try:
            _validate_workspace_bindings(config)
            options = row["provider"]["options"]
            actual = {
                "kubernetes_context": options["KUBERNETES_CONTEXT"]["value"],
                "namespace": options["KUBERNETES_NAMESPACE"]["value"],
                "kubeconfig": options["KUBERNETES_CONFIG"]["value"],
                "repository": row["source"]["gitRepository"],
                "revision": row["source"]["gitCommit"],
                "recipe": row["devContainerPath"],
            }
            if any(config[field] != actual[field] for field in _WORKSPACE_BINDINGS):
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise ResolutionError("workspace_binding_changed") from None
    return row, workspace, prefix


def _devsy(config):
    row, workspace, prefix = _devsy_workspace(config)
    context = _name(config.get("context", "default"))
    state = _run([*prefix, "status", workspace, "--timeout", "10s"], True)
    if (
        not isinstance(state, dict)
        or state.get("id") != workspace
        or state.get("context") != context
    ):
        raise ResolutionError("workspace_identity_mismatch")
    if state.get("state") != "Running":
        raise ResolutionError("workspace_not_running")
    # Devsy owns this alias and port. Never synthesize localhost:port or start it.
    alias = workspace + ".devsy"
    ssh_file = row.get("sshConfigIncludePath") or row.get("sshConfigPath")
    config_args = []
    if ssh_file:
        if not isinstance(ssh_file, str):
            raise ResolutionError("invalid_ssh_configuration_path")
        ssh_file = str(Path(ssh_file).expanduser())
        validate_cwd(ssh_file)
        config_args = ["-F", ssh_file]
    ssh = _run(
        [
            "ssh",
            "-G",
            *config_args,
            "-o",
            "BatchMode=yes",
            "-o",
            "ForwardAgent=no",
            alias,
        ]
    )
    fields = dict(line.split(None, 1) for line in ssh.splitlines() if " " in line)
    if fields.get("user") != config.get("user", "vscode"):
        raise ResolutionError("ssh_user_mismatch")
    try:
        loopback = ipaddress.ip_address(fields.get("hostname", "")).is_loopback
        port = int(fields.get("port", "0"))
    except ValueError:
        loopback, port = False, 0
    # Current tested Devsy Desktop route is an existing loopback TCP tunnel.
    # ProxyCommand may start/reconfigure Devsy; reject instead of causing lifecycle effects.
    if (
        not loopback
        or not 1 <= port <= 65535
        or fields.get("proxycommand", "none") != "none"
        or fields.get("proxyjump", "none") != "none"
    ):
        raise ResolutionError("devsy_active_tcp_route_required")
    uid = _name(row.get("uid"))
    options = (
        *config_args,
        "-o",
        "Hostname=" + fields["hostname"],
        "-p",
        str(port),
        "-l",
        fields["user"],
        "-o",
        "ProxyCommand=none",
        "-o",
        "ProxyJump=none",
        "-o",
        "RemoteCommand=none",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "PermitLocalCommand=no",
    )
    return alias, options, uid, workspace


def resolve_target(name: str) -> Target:
    configs, dynamic_error = _configuration_snapshot(name)
    if not isinstance(name, str) or name not in configs:
        if dynamic_error is not None:
            raise dynamic_error
        raise ResolutionError("Target is not configured")
    config = configs[name]
    if config.get("transport") == "container":
        if set(config) != {"transport", "engine", "endpoint", "owner", "worker"}:
            raise ResolutionError("invalid_target_configuration")
        worker = ContainerProvider(
            config["engine"], config["endpoint"], config["owner"], config["worker"]
        ).resolve()
        return Target(
            f"{worker.engine.name}:{worker.container_id}",
            worker.codex_bin,
            logical_name=name,
            container=worker,
        )
    allowed = {
        "transport",
        "codex_bin",
        "socket_path",
        "destination",
        "context",
        "workspace",
        "source",
        "provider",
        "workspace_uid",
        "user",
    } | _WORKSPACE_BINDINGS
    if set(config) - allowed:
        raise ResolutionError("invalid_target_configuration")
    transport = config.get("transport")
    binary = config.get(
        "codex_bin",
        "codex" if transport == "local" else "/home/vscode/.local/bin/codex",
    )
    if (
        not isinstance(binary, str)
        or not binary
        or "\x00" in binary
        or any(c.isspace() for c in binary)
    ):
        raise ResolutionError("invalid_codex_binary")
    socket = config.get("socket_path")
    if socket is not None:
        validate_cwd(socket)
    options, uid, workspace = (), None, None
    if transport == "local":
        destination = "local"
    elif transport == "ssh":
        destination = _name(config.get("destination"))
        if destination == "local":
            raise ResolutionError("invalid_ssh_destination")
    elif transport == "devsy":
        destination, options, uid, workspace = _devsy(config)
    elif transport == "devsy-kubernetes":
        if not config.get("workspace_uid"):
            raise ResolutionError("workspace_uid_required")
        if config.get("provider") != "kubernetes" or not config.get("workspace"):
            raise ResolutionError("pinned_kubernetes_workspace_required")
        if config.get("user", "vscode") != "vscode":
            raise ResolutionError("kubernetes_worker_user_must_be_vscode")
        row, workspace, _ = _devsy_workspace(config)
        worker = KubernetesWorker.resolve(row, binary)
        return Target(
            "kubernetes:" + worker.namespace + "/" + worker.pod,
            binary,
            socket,
            name,
            workspace_uid=worker.workspace_uid,
            workspace_id=workspace,
            container=worker,
        )
    else:
        raise ResolutionError("unsupported_target_transport")
    return Target(destination, binary, socket, name, options, uid, workspace)


def validate_cwd(cwd: str) -> str:
    if not isinstance(cwd, str) or not cwd or any(ord(c) < 32 for c in cwd):
        raise ValueError("cwd must be an absolute path")
    path = PurePosixPath(cwd)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("cwd must be an absolute normalized path")
    return cwd


def validate_identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field_name} must be a non-empty single-line identifier")
    return value
