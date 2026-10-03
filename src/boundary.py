"""Operator-owned logical targets. No lifecycle or caller-selected transport."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

from codex_rpc import Target

_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_IDENTIFIER = re.compile(r"^[^\s\r\n]+$")


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


def configurations():
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


def _devsy(config):
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
    ssh = _run(["ssh", "-G", "-o", "BatchMode=yes", "-o", "ForwardAgent=no", alias])
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
    return alias


def resolve_target(name: str) -> Target:
    configs = configurations()
    if not isinstance(name, str) or name not in configs:
        raise ResolutionError("Target is not configured")
    config = configs[name]
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
    }
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
    if transport == "local":
        destination = "local"
    elif transport == "ssh":
        destination = _name(config.get("destination"))
        if destination == "local":
            raise ResolutionError("invalid_ssh_destination")
    elif transport == "devsy":
        destination = _devsy(config)
    else:
        raise ResolutionError("unsupported_target_transport")
    return Target(destination, binary, socket, name)


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
