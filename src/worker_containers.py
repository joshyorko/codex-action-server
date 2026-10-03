"""Explicit operator lifecycle and read-only routes for disposable containers.

The engine socket is operator authority. No values here come from action callers.
Devsy retains its native lifecycle; this adapter is not an RPC action or scheduler.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


LABEL = "io.codex-action-server.worker."
ROOT = "/workspaces/codex-action-server"
DEFINITION = ".devcontainer/remote-worker/devcontainer.json"
READY = "/home/vscode/.codex-worker-setup-complete"
CODEX_HOME = "/home/vscode/.codex"
CODEX_BIN = "/home/vscode/.local/bin/codex"
_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}\Z")
_ID = re.compile(r"[a-f0-9]{64}\Z")
_SHA = re.compile(r"[a-f0-9]{40}\Z")


class WorkerError(ValueError):
    """A stable operator-safe error without engine output or credentials."""


def _name(value):
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise WorkerError("invalid_worker_configuration")
    return value


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise WorkerError("invalid_container_identity")
    return value


def _path(value):
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or any(c.isspace() or ord(c) < 32 for c in value)
        or ".." in PurePosixPath(value).parts
        or "\x00" in value
    ):
        raise WorkerError("invalid_worker_configuration")
    return value


@dataclass(frozen=True)
class ContainerEngine:
    name: str
    endpoint: str

    def __post_init__(self):
        if self.name not in {"docker", "podman"}:
            raise WorkerError("unsupported_container_engine")
        if not isinstance(self.endpoint, str) or not self.endpoint.startswith(
            "unix:///"
        ):
            raise WorkerError("local_engine_socket_required")
        path = self.endpoint[len("unix://") :]
        _path(path)
        if "?" in path or "#" in path:
            raise WorkerError("local_engine_socket_required")

    def command(self, args):
        prefix = (
            ["docker", "--host", self.endpoint]
            if self.name == "docker"
            else ["podman", "--remote", "--url", self.endpoint]
        )
        return [*prefix, *args]

    def run(self, args, *, timeout=30, archive=None):
        try:
            result = subprocess.run(
                self.command(args),
                input=archive,
                text=archive is None,
                capture_output=True,
                timeout=timeout,
                check=True,
            )
            output = result.stdout
            if len(output) > 8 * 1024 * 1024:
                raise WorkerError("container_output_too_large")
            return output.decode() if isinstance(output, bytes) else output
        except (OSError, subprocess.SubprocessError, UnicodeError):
            raise WorkerError("container_command_failed:" + args[0]) from None


@dataclass(frozen=True)
class Worker:
    engine: ContainerEngine
    owner: str
    name: str
    container_id: str
    source_commit: str
    codex_home: str
    codex_bin: str
    running: bool
    paused: bool = False
    restarting: bool = False

    def command(self, args):
        """Pin version/proxy to the resolved container, never its reusable name."""
        return self.engine.command(
            [
                "exec",
                "--interactive",
                "--user",
                "vscode",
                "--env",
                "CODEX_HOME=" + self.codex_home,
                self.container_id,
                self.codex_bin,
                *args,
            ]
        )


@dataclass(frozen=True)
class SourceRecipe:
    root: Path
    commit: str
    definition: dict
    archive: bytes

    @classmethod
    def load(cls, path):
        root = Path(path).resolve()
        try:
            subprocess.run(
                ["git", "-C", str(root), "diff", "--quiet", "HEAD", "--"],
                check=True,
                capture_output=True,
                timeout=15,
            )
            commit = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout.strip()
            definition = json.loads(
                subprocess.run(
                    ["git", "-C", str(root), "show", commit + ":" + DEFINITION],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=15,
                ).stdout
            )
            archive = subprocess.run(
                ["git", "-C", str(root), "archive", "--format=tar", commit],
                check=True,
                capture_output=True,
                timeout=30,
            ).stdout
        except (OSError, subprocess.SubprocessError, ValueError):
            raise WorkerError("clean_committed_source_required") from None
        keys = {
            "name",
            "image",
            "remoteUser",
            "waitFor",
            "postCreateCommand",
            "postStartCommand",
        }
        if (
            not _SHA.fullmatch(commit)
            or not isinstance(definition, dict)
            or set(definition) != keys
            or definition["remoteUser"] != "vscode"
            or definition["waitFor"] != "postCreateCommand"
            or not isinstance(definition["image"], str)
            or not re.fullmatch(
                r"[a-zA-Z0-9._:/-]+@sha256:[a-f0-9]{64}", definition["image"]
            )
            or any(
                not isinstance(definition[k], str) or not definition[k]
                for k in ("postCreateCommand", "postStartCommand")
            )
        ):
            raise WorkerError("unsupported_worker_recipe")
        return cls(root, commit, definition, archive)


class ContainerProvider:
    def __init__(self, engine, endpoint, owner, worker):
        self.engine = ContainerEngine(engine, endpoint)
        self.owner, self.name = _name(owner), _name(worker)

    def _ids(self):
        output = self.engine.run(
            [
                "ps",
                "--all",
                "--no-trunc",
                "--filter",
                f"label={LABEL}schema=1",
                "--filter",
                f"label={LABEL}owner={self.owner}",
                "--filter",
                f"label={LABEL}name={self.name}",
                "--format",
                "{{.ID}}",
            ]
        )
        return [_id(value) for value in output.splitlines() if value]

    def _inspect(self, container_id):
        _id(container_id)
        try:
            rows = json.loads(self.engine.run(["container", "inspect", container_id]))
            if not isinstance(rows, list) or len(rows) != 1:
                raise WorkerError("invalid_container_response")
            row = rows[0]
            config, state, host = row["Config"], row["State"], row["HostConfig"]
            labels = config["Labels"]
            if (
                row["Id"] != container_id
                or labels.get(LABEL + "schema") != "1"
                or labels.get(LABEL + "owner") != self.owner
                or labels.get(LABEL + "name") != self.name
                or config["User"] != "vscode"
                or labels.get(LABEL + "home") != CODEX_HOME
                or labels.get(LABEL + "binary") != CODEX_BIN
                or not _SHA.fullmatch(labels.get(LABEL + "source", ""))
            ):
                raise WorkerError("worker_identity_mismatch")
            if (
                row["Mounts"] != []
                or host["PidMode"] not in {"", "private"}
                or host["Privileged"] is not False
                or host.get("Binds")
                or host.get("VolumesFrom")
            ):
                raise WorkerError("worker_not_isolated")
            if not isinstance(state["Running"], bool):
                raise WorkerError("invalid_container_response")
            return Worker(
                self.engine,
                self.owner,
                self.name,
                container_id,
                labels[LABEL + "source"],
                CODEX_HOME,
                CODEX_BIN,
                state["Running"],
                bool(state.get("Paused", False)),
                bool(state.get("Restarting", False)),
            )
        except (KeyError, TypeError, AttributeError, json.JSONDecodeError):
            raise WorkerError("invalid_container_response") from None

    def status(self):
        ids = self._ids()
        if len(ids) > 1:
            raise WorkerError("worker_ambiguous")
        return self._inspect(ids[0]) if ids else None

    def resolve(self):
        worker = self.status()
        if worker is None:
            raise WorkerError("worker_not_found")
        if not worker.running or worker.paused or worker.restarting:
            raise WorkerError("worker_not_running")
        return worker

    def _pinned(self, expected):
        _id(expected)
        worker = self.status()
        if worker is None:
            raise WorkerError("worker_not_found")
        if worker.container_id != expected:
            raise WorkerError("worker_identity_changed")
        return worker

    def _exec(self, worker, args, *, user="vscode", timeout=30):
        return self.engine.run(
            [
                "exec",
                "--user",
                user,
                "--workdir",
                ROOT,
                worker.container_id,
                *args,
            ],
            timeout=timeout,
        )

    def create(self, source, headroom_url):
        # Do not repeat an uncertain create; reconcile it using status instead.
        if self.status() is not None:
            raise WorkerError("worker_already_exists")
        if not isinstance(headroom_url, str) or not re.fullmatch(
            r"https?://[A-Za-z0-9._:/-]+", headroom_url
        ):
            raise WorkerError("invalid_headroom_endpoint")
        recipe = SourceRecipe.load(source)
        labels = {
            "schema": "1",
            "owner": self.owner,
            "name": self.name,
            "source": recipe.commit,
            "home": CODEX_HOME,
            "binary": CODEX_BIN,
        }
        args = [
            "create",
            "--name",
            f"codex-worker-{self.owner}-{self.name}",
            "--init",
            "--user",
            "vscode",
            "--workdir",
            ROOT,
            "--entrypoint",
            "/bin/sh",
            "--env",
            "HOME=/home/vscode",
            "--env",
            "CODEX_HOME=" + CODEX_HOME,
            "--env",
            "CODEX_WORKER_HEADROOM_URL=" + headroom_url,
        ]
        for key, value in labels.items():
            args.extend(["--label", LABEL + key + "=" + value])
        args.extend([recipe.definition["image"], "-c", "exec sleep infinity"])
        cid = _id(self.engine.run(args, timeout=300).strip())
        worker = self._inspect(cid)
        # All subsequent steps stay pinned to this ID. Failure leaves it for
        # explicit reconciliation/deletion, never a second automatic create.
        self.engine.run(["start", cid])
        self._exec(worker, ["mkdir", "-p", ROOT], user="root")
        self.engine.run(["cp", "-", cid + ":" + ROOT], archive=recipe.archive)
        self._exec(worker, ["chown", "-R", "vscode:vscode", ROOT], user="root")
        self._exec(
            worker,
            ["/bin/sh", "-c", recipe.definition["postCreateCommand"]],
            timeout=900,
        )
        self._exec(worker, ["touch", READY])
        self._exec(
            worker,
            ["/bin/sh", "-c", recipe.definition["postStartCommand"]],
            timeout=180,
        )
        return self._inspect(cid)

    def start(self, expected_container_id):
        worker = self._pinned(expected_container_id)
        if worker.paused or worker.restarting:
            raise WorkerError("worker_paused_or_restarting")
        if not worker.running:
            self.engine.run(["start", worker.container_id])
        # A partial create is not permission to replay installer side effects.
        try:
            self._exec(worker, ["test", "-f", READY])
        except WorkerError:
            raise WorkerError("worker_setup_incomplete_delete_and_recreate") from None
        if worker.running:
            self._exec(worker, ["/bin/bash", "scripts/remote/verify.sh"], timeout=180)
        else:
            # Read the hook from the copied source, not the operator's newer HEAD.
            self._exec(
                worker,
                [
                    "/home/linuxbrew/.linuxbrew/bin/python3",
                    "-c",
                    "import json,subprocess; "
                    f"d=json.load(open({DEFINITION!r})); "
                    "subprocess.run(['/bin/sh','-c',d['postStartCommand']],check=True)",
                ],
                timeout=180,
            )
        return self._inspect(worker.container_id)

    def stop(self, expected_container_id):
        worker = self._pinned(expected_container_id)
        if worker.paused or worker.restarting:
            raise WorkerError("worker_paused_or_restarting")
        if worker.running:
            self.engine.run(["stop", worker.container_id], timeout=60)
        return self._inspect(worker.container_id)

    def delete(self, expected_container_id):
        worker = self._pinned(expected_container_id)
        if worker.running:
            raise WorkerError("worker_must_be_stopped")
        self.engine.run(["rm", "--volumes", worker.container_id])
