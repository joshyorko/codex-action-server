#!/usr/bin/env python3
"""Run genuine provider acceptance through an explicitly forwarded engine socket."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import uuid

DAGGER_VERSION = "0.21.10"
PODMAN_REMOTE_VERSION = "4.9.3"
# Verified against the official v4.9.3 release's shasums, not resolved at runtime.
PODMAN_REMOTE_SHA256 = {
    "amd64": "b21cad103bda0c71648e424b40730bb59e668fc5bb98ed17209c9b1c93880991",
    "arm64": "a432625b0a697ddcd7a74044d2a5e1ff28026df1985bafe15c30194051c157f5",
}
DOCKER_CLI_IMAGE = (
    "docker:28.5.2-cli@sha256:"
    "625d9431a9f54c5a2bc90f24f0e1c3d55b1349fd857dd85035f98c2c9acbdd4d"
)


def command(args, *, cwd=None, timeout=120):
    return subprocess.run(
        args, cwd=cwd, check=True, capture_output=True, text=True, timeout=timeout
    ).stdout.strip()


def prepare_source(source: Path, destination: Path) -> str:
    """Copy committed objects only, preserving HEAD without copying Git credentials."""
    source = source.resolve()
    if command(["git", "status", "--porcelain"], cwd=source):
        raise ValueError("Acceptance requires a clean committed source checkout")
    commit = command(["git", "rev-parse", "HEAD"], cwd=source)
    destination.mkdir()
    command(["git", "init", "-q"], cwd=destination)
    command(
        ["git", "fetch", "--no-tags", "--depth=1", str(source), commit],
        cwd=destination,
    )
    command(["git", "checkout", "-q", "--detach", "FETCH_HEAD"], cwd=destination)
    if command(["git", "rev-parse", "HEAD"], cwd=destination) != commit:
        raise ValueError("Source commit changed during snapshot")
    return commit


def validate_socket(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("The engine socket must be an absolute path")
    try:
        is_socket = stat.S_ISSOCK(path.stat().st_mode)
    except OSError:
        is_socket = False
    if not is_socket:
        raise ValueError("An existing Unix socket is required")
    return path


def full_container_id(worker) -> str:
    identity = worker.get("container_id") if isinstance(worker, dict) else None
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
        raise ValueError("Cleanup requires a full container identity")
    return identity


def worker_prefix(source: Path, engine: str, endpoint: str, owner: str) -> list[str]:
    return [
        sys.executable,
        str(source / "scripts/remote/worker.py"),
        "--engine",
        engine,
        "--endpoint",
        endpoint,
        "--owner",
        owner,
        "--worker",
        "acceptance",
    ]


def worker_status(source: Path, engine: str, endpoint: str, owner: str):
    return json.loads(
        command([*worker_prefix(source, engine, endpoint, owner), "status"])
    )


def cleanup(source: Path, engine: str, endpoint: str, owner: str) -> dict:
    """Reconcile once, then let the provider recheck ownership and the exact ID."""
    prefix = worker_prefix(source, engine, endpoint, owner)
    worker = json.loads(command([*prefix, "status"]))
    if worker is None:
        return {"status": "absent"}
    identity = full_container_id(worker)
    if worker.get("running"):
        command([*prefix, "stop", "--container-id", identity])
    command([*prefix, "delete", "--container-id", identity])
    if json.loads(command([*prefix, "status"])) is not None:
        raise RuntimeError("Scoped worker remains after cleanup")
    return {"status": "deleted", "container_id": identity}


def run_host_controller(image_archive: Path, args, owner: str, temporary: Path) -> dict:
    """Run the Dagger-built test image with only the selected Podman socket bound."""
    if any(character in args.socket for character in ",\r\n"):
        raise ValueError("A controller socket path cannot contain commas or newlines")
    prefix = ["docker", "--host", "unix://" + args.controller_socket]
    loaded = command([*prefix, "load", "--input", str(image_archive)], timeout=300)
    images = re.findall(
        r"^Loaded image ID: (sha256:[0-9a-f]{64})$", loaded, re.MULTILINE
    )
    if len(images) != 1:
        raise RuntimeError("Expected one immutable image ID from controller image load")
    image_id = images[0]
    nonce = uuid.uuid4().hex
    name = "provider-acceptance-controller-" + nonce
    label = "io.codex-action-server.acceptance-controller"
    identity = None
    try:
        identity = full_container_id(
            {
                "container_id": command(
                    [
                        *prefix,
                        "create",
                        "--name",
                        name,
                        "--label",
                        label + "=" + nonce,
                        "--mount",
                        "type=bind,source="
                        + args.socket
                        + ",target=/run/provider.sock",
                        "--workdir",
                        "/workspace",
                        "--entrypoint",
                        "python",
                        image_id,
                        "ci/provider_acceptance.py",
                        "--engine",
                        "podman",
                        "--endpoint",
                        "unix:///run/provider.sock",
                        "--owner",
                        owner,
                        "--output",
                        "/evidence/result.json",
                    ],
                    timeout=30,
                )
            }
        )
        command([*prefix, "start", identity], timeout=30)
        exit_code = command([*prefix, "wait", identity], timeout=1900)
        evidence = temporary / "controller-result.json"
        command(
            [*prefix, "cp", identity + ":/evidence/result.json", str(evidence)],
            timeout=30,
        )
        report = json.loads(evidence.read_text())
        report["test_transport"] = "direct_bind_socket"
        report["controller_id"] = identity
        report["controller_image_id"] = image_id
        if exit_code != "0":
            report["status"] = "failed"
            report["controller_exit_code"] = exit_code
        return report
    finally:
        if identity is None:
            # A timed-out create may have succeeded. Reconcile this unpredictable
            # run-specific name and label once, then remove only its verified ID.
            try:
                rows = json.loads(
                    command([*prefix, "container", "inspect", name], timeout=15)
                )
            except subprocess.CalledProcessError:
                rows = []
            if rows:
                if (
                    len(rows) != 1
                    or rows[0].get("Config", {}).get("Labels", {}).get(label) != nonce
                ):
                    raise RuntimeError(
                        "Controller cleanup identity could not be verified"
                    )
                identity = full_container_id({"container_id": rows[0].get("Id")})
        if identity is not None:
            command([*prefix, "rm", "--force", identity], timeout=30)


async def acceptance(snapshot: Path, args, owner: str) -> dict:
    # Lazy import keeps source/cleanup checks usable without a Dagger installation.
    import dagger

    async with dagger.Connection(dagger.Config(log_output=sys.stderr)) as client:
        docker_cli = client.container().from_(DOCKER_CLI_IMAGE)
        runner = (
            client.container()
            .from_("python:3.12-slim-bookworm")
            .with_mounted_cache(
                "/var/cache/apt",
                client.cache_volume("provider-acceptance-apt-bookworm-v1"),
            )
            .with_mounted_cache(
                "/var/lib/apt/lists",
                client.cache_volume("provider-acceptance-apt-lists-bookworm-v1"),
                sharing=dagger.CacheSharingMode.LOCKED,
            )
            .with_exec(
                [
                    "sh",
                    "-ec",
                    "rm -f /etc/apt/apt.conf.d/docker-clean; "
                    "apt-get update; apt-get install -y --no-install-recommends "
                    "ca-certificates git podman",
                ]
            )
            .with_file(
                "/usr/local/bin/docker", docker_cli.file("/usr/local/bin/docker")
            )
            .with_mounted_cache(
                "/root/.cache/pip",
                client.cache_volume("provider-acceptance-pip-py312-v1"),
            )
            .with_exec(["python", "-m", "pip", "install", "websockets==15.0.1"])
        )
        if args.engine == "podman":
            for architecture in PODMAN_REMOTE_SHA256:
                runner = runner.with_file(
                    f"/tmp/podman-{architecture}.tar.gz",
                    client.http(
                        "https://github.com/podman-container-tools/podman/releases/download/"
                        f"v{PODMAN_REMOTE_VERSION}/podman-remote-static-linux_{architecture}.tar.gz"
                    ),
                )
            runner = runner.with_exec(
                [
                    "sh",
                    "-ec",
                    'case "$(uname -m)" in '
                    f"x86_64) arch=amd64; digest={PODMAN_REMOTE_SHA256['amd64']} ;; "
                    f"aarch64) arch=arm64; digest={PODMAN_REMOTE_SHA256['arm64']} ;; "
                    '*) echo "Unsupported Podman client architecture" >&2; exit 2 ;; esac; '
                    'printf "%s  /tmp/podman-%s.tar.gz\\n" "$digest" "$arch" | sha256sum -c -; '
                    'tar -xzf "/tmp/podman-$arch.tar.gz" -C /tmp "bin/podman-remote-static-linux_$arch"; '
                    'install -m 0755 "/tmp/bin/podman-remote-static-linux_$arch" /usr/local/bin/podman; '
                    'test "$(command -v podman)" = /usr/local/bin/podman; '
                    'podman --version; rm -rf /tmp/bin /tmp/podman-*.tar.gz',
                ]
            )
        runner = runner.with_directory(
            "/workspace", client.host().directory(str(snapshot))
        ).with_workdir("/workspace")
        test_command = [
            "python",
            "ci/provider_acceptance.py",
            "--engine",
            args.engine,
            "--endpoint",
            "unix:///run/provider.sock",
            "--owner",
            owner,
            "--output",
            "/evidence/result.json",
        ]
        if args.engine == "podman":
            with tempfile.TemporaryDirectory(
                prefix="provider-controller-"
            ) as directory:
                temporary = Path(directory)
                archive = temporary / "controller.tar"
                await runner.export(
                    str(archive), media_types=dagger.ImageMediaTypes.DOCKER
                )
                return await asyncio.to_thread(
                    run_host_controller, archive, args, owner, temporary
                )
        # External lifecycle effects must always execute, even if source is unchanged.
        executed = (
            runner.with_unix_socket(
                "/run/provider.sock", client.host().unix_socket(args.socket)
            )
            .with_env_variable("ACCEPTANCE_RUN_NONCE", uuid.uuid4().hex)
            .with_exec(test_command)
        )
        return json.loads(await executed.file("/evidence/result.json").contents())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, choices=["docker", "podman"])
    parser.add_argument(
        "--socket", required=True, help="Explicit host Unix engine socket"
    )
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--owner", default="acceptance-" + uuid.uuid4().hex)
    parser.add_argument(
        "--output", type=Path, default=Path("/tmp/provider-acceptance.json")
    )
    parser.add_argument("--cleanup-only", action="store_true")
    parser.add_argument(
        "--controller-socket",
        help="Explicit local Docker socket for the Podman test controller",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", args.owner):
        parser.error("owner must be a unique run-scoped identifier")
    endpoint = "unix://" + str(validate_socket(args.socket))
    if args.engine == "podman" and not args.cleanup_only:
        if not args.controller_socket:
            parser.error(
                "Podman acceptance requires --controller-socket for local Docker"
            )
        args.controller_socket = str(validate_socket(args.controller_socket))
    if args.cleanup_only:
        print(
            json.dumps(
                cleanup(args.source.resolve(), args.engine, endpoint, args.owner)
            )
        )
        return 0
    report = {
        "status": "failed",
        "engine": args.engine,
        "owner": args.owner,
        "dagger_version": DAGGER_VERSION,
        "stage": "source_snapshot",
    }
    # This process is on the host running Dagger, outside both worker and test runner.
    canary = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2400)"])
    try:
        with tempfile.TemporaryDirectory(prefix="provider-acceptance-") as temporary:
            snapshot = Path(temporary) / "source"
            report["source_commit"] = prepare_source(args.source, snapshot)
            report["stage"] = "preflight_absent"
            if worker_status(snapshot, args.engine, endpoint, args.owner) is not None:
                raise RuntimeError(
                    "Run scope already has a worker; refusing to reuse it"
                )
            report["scope_owned"] = True
            report["stage"] = "dagger_acceptance"
            try:
                native = asyncio.run(
                    asyncio.wait_for(
                        acceptance(snapshot, args, args.owner), timeout=2100
                    )
                )
                report["acceptance"] = native
                if native.get("status") != "passed":
                    if (
                        native.get("cleanup", {}).get("status")
                        == "deferred_to_host_diagnostics"
                    ):
                        from worker_probe import read_only_transport_probe

                        expected = full_container_id(native.get("failure_diagnostics"))
                        current = worker_status(
                            snapshot, args.engine, endpoint, args.owner
                        )
                        if full_container_id(current) != expected:
                            raise RuntimeError(
                                "Worker changed before host transport comparison"
                            )
                        report["host_transport"] = read_only_transport_probe(
                            args.engine, endpoint, expected
                        )
                    raise RuntimeError("Real-provider acceptance failed; see its stage")
            finally:
                report["cleanup"] = cleanup(snapshot, args.engine, endpoint, args.owner)
            if canary.poll() is not None:
                raise RuntimeError("Runner-host canary was interrupted")
            report["host_canary_alive_after_lifecycle"] = True
            report["status"] = "passed"
            report["stage"] = "complete"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        report["host_canary_alive_before_teardown"] = canary.poll() is None
        canary.terminate()
        canary.wait(timeout=10)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
