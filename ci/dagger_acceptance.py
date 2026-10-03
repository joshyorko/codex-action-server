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


def cleanup(source: Path, engine: str, endpoint: str, owner: str) -> dict:
    """Reconcile once, then let the provider recheck ownership and the exact ID."""
    prefix = [
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
            .with_directory("/workspace", client.host().directory(str(snapshot)))
            .with_workdir("/workspace")
            .with_unix_socket(
                "/run/provider.sock", client.host().unix_socket(args.socket)
            )
            # Engine calls mutate external state. Never reuse their cached result.
            .with_env_variable("ACCEPTANCE_RUN_NONCE", uuid.uuid4().hex)
            .with_exec(
                [
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
            )
        )
        return json.loads(await runner.file("/evidence/result.json").contents())


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
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", args.owner):
        parser.error("owner must be a unique run-scoped identifier")
    endpoint = "unix://" + str(validate_socket(args.socket))
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
            report["stage"] = "dagger_acceptance"
            try:
                native = asyncio.run(
                    asyncio.wait_for(
                        acceptance(snapshot, args, args.owner), timeout=2100
                    )
                )
                report["acceptance"] = native
                if native.get("status") != "passed":
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
