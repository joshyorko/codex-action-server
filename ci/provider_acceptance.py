#!/usr/bin/env python3
"""Credential-free real-engine checks, executed inside the Dagger test container.

Always writes a verdict and exits zero so Dagger can return failure evidence.
The outer driver treats any verdict other than passed as a failed process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from dagger_acceptance import cleanup, command, full_container_id

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def cli(args, *operation, check=True, timeout=1000):
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "ci/worker_probe.py"),
            "--engine",
            args.engine,
            "--endpoint",
            args.endpoint,
            "--owner",
            args.owner,
            "--worker",
            "acceptance",
            *operation,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and process.returncode:
        # No credentials are admitted. Keep this small enough for failure artifacts.
        raise RuntimeError(
            f"worker {' '.join(operation[:1])} failed ({process.returncode}): "
            + process.stderr[-4000:]
        )
    return json.loads(process.stdout) if check else process


def engine(args, *operation, timeout=120):
    prefix = (
        ["docker", "--host", args.endpoint]
        if args.engine == "docker"
        else ["podman", "--remote", "--url", args.endpoint]
    )
    return command([*prefix, *operation], timeout=timeout)


def worker_exec(args, worker, *operation):
    return engine(
        args,
        "exec",
        "--user",
        "vscode",
        "--env",
        "CODEX_HOME=" + worker["codex_home"],
        full_container_id(worker),
        *operation,
    )


def smoke(args, worker, target_file):
    from boundary import resolve_target
    from codex_rpc import Client

    target_file.write_text(
        json.dumps(
            {
                "targets": {
                    "acceptance": {
                        "transport": "container",
                        "engine": args.engine,
                        "endpoint": args.endpoint,
                        "owner": args.owner,
                        "worker": "acceptance",
                    }
                }
            }
        )
    )
    os.environ["CODEX_ACTION_TARGETS"] = str(target_file)
    target = resolve_target("acceptance")
    with Client(target, timeout=30) as client:
        if client.metadata.get("codexHome") != worker["codex_home"]:
            raise RuntimeError("Initialize returned the wrong Codex home")
        diagnostics = client.request("server/diagnostics", {})
        threads = client.request("thread/list", {"limit": 1})
        if not isinstance(diagnostics, dict):
            raise RuntimeError("Native diagnostics was not an object")
        if not isinstance(threads, dict) or not isinstance(threads.get("data"), list):
            raise RuntimeError("Native thread discovery was not a data list")
        # Do not store diagnostic content, thread contents, or account data.
        evidence = {
            "codex_home": client.metadata["codexHome"],
            "server": client.metadata.get("userAgent"),
            "diagnostics_read": True,
            "thread_list_read": True,
            "discovered_threads": len(threads["data"]),
        }
    return target, evidence


def check_isolation(args, worker):
    inspect = json.loads(engine(args, "inspect", full_container_id(worker)))[0]
    if inspect.get("Mounts"):
        raise RuntimeError("Worker unexpectedly has mounts")
    if inspect.get("HostConfig", {}).get("Privileged"):
        raise RuntimeError("Worker unexpectedly has privileged access")
    if not inspect.get("State", {}).get("Running"):
        raise RuntimeError("Created worker is not running")
    return {
        "image_reference": inspect["Config"]["Image"],
        "image_id": inspect["Image"],
        "mount_count": 0,
        "privileged": False,
    }


def deadline(*_):
    raise TimeoutError("Real-provider acceptance exceeded 30 minutes")


def run(args, report):
    from codex_rpc import Client

    report["stage"] = "engine_version"
    report["engine_version"] = engine(args, "version")
    report["source_commit"] = command(["git", "rev-parse", "HEAD"], cwd=ROOT)
    recipe = json.loads(
        (ROOT / ".devcontainer/remote-worker/devcontainer.json").read_text()
    )
    report["recipe_image"] = recipe["image"]
    report["stage"] = "recipe_image_pull"
    engine(args, "pull", recipe["image"], timeout=600)
    report["recipe_image_available"] = True
    report["stage"] = "preflight_absent"
    if cli(args, "status") is not None:
        raise RuntimeError("Run scope already has a worker; refusing to reuse it")
    report["worker_scope_was_absent"] = True
    with tempfile.TemporaryDirectory(prefix="native-acceptance-") as temporary:
        target_file = Path(temporary) / "targets.json"
        report["stage"] = "create"
        first = cli(
            args,
            "create",
            "--source",
            str(ROOT),
            "--headroom-url",
            "http://127.0.0.1:9/v1",
        )
        first_id = full_container_id(first)
        report["first_worker"] = first
        if first["source_commit"] != report["source_commit"]:
            raise RuntimeError("Worker source SHA differs from the supplied checkout")
        report["isolation"] = check_isolation(args, first)
        if report["isolation"]["image_reference"] != recipe["image"]:
            raise RuntimeError("Worker did not use the reviewed recipe image")
        report["stage"] = "native_read_only"
        stale_target, report["first_rpc"] = smoke(args, first, target_file)
        versions = {}
        for name, path in (
            ("codex", first["codex_bin"]),
            ("headroom", "/home/linuxbrew/.linuxbrew/bin/headroom"),
            ("rtk", "/home/linuxbrew/.linuxbrew/bin/rtk"),
            ("gh", "/home/linuxbrew/.linuxbrew/bin/gh"),
            ("python", "/home/linuxbrew/.linuxbrew/bin/python3"),
        ):
            versions[name] = worker_exec(args, first, path, "--version")
        report["installed_versions"] = versions
        config = worker_exec(args, first, "cat", first["codex_home"] + "/config.toml")
        config_hash = hashlib.sha256(config.encode()).hexdigest()
        report["config_sha256_before_stop"] = config_hash
        # Change only this disposable worker. Any create-hook replay must fail.
        worker_exec(
            args,
            first,
            "/bin/bash",
            "-c",
            "printf '#!/bin/bash\nexit 97\n' > scripts/remote/setup.sh",
        )
        report["create_hook_disabled_before_restart"] = True
        # Independently prove the worker's home survived restart.
        marker = first["codex_home"] + "/acceptance-retained-marker"
        worker_exec(args, first, "touch", marker)
        report["stage"] = "stop"
        stopped = cli(args, "stop", "--container-id", first_id)
        if stopped["running"] or cli(args, "status")["running"]:
            raise RuntimeError("Stop did not leave the selected worker stopped")
        report["stage"] = "start"
        resumed = cli(args, "start", "--container-id", first_id)
        if full_container_id(resumed) != first_id or not resumed["running"]:
            raise RuntimeError("Start did not resume the same worker identity")
        worker_exec(args, resumed, "test", "-f", marker)
        retained_config = worker_exec(
            args, resumed, "cat", resumed["codex_home"] + "/config.toml"
        )
        if hashlib.sha256(retained_config.encode()).hexdigest() != config_hash:
            raise RuntimeError("Stop/start changed the retained Codex config")
        _, report["resumed_rpc"] = smoke(args, resumed, target_file)
        report["retained_home_and_config"] = True
        report["restart_skipped_create_hook"] = True
        report["stage"] = "delete"
        cli(args, "stop", "--container-id", first_id)
        cli(args, "delete", "--container-id", first_id)
        if cli(args, "status") is not None:
            raise RuntimeError("Deleted worker still resolves")
        report["stage"] = "recreate"
        second = cli(
            args,
            "create",
            "--source",
            str(ROOT),
            "--headroom-url",
            "http://127.0.0.1:9/v1",
        )
        second_id = full_container_id(second)
        report["second_worker"] = second
        if second_id == first_id:
            raise RuntimeError("Recreation reused the deleted container identity")
        report["stage"] = "reject_stale_lifecycle"
        rejected = cli(args, "stop", "--container-id", first_id, check=False)
        if rejected.returncode == 0:
            raise RuntimeError("Stale identity was allowed to stop the replacement")
        if not cli(args, "status")["running"]:
            raise RuntimeError("Stale stop affected the replacement worker")
        report["stale_stop_rejected"] = True
        report["stage"] = "reject_stale_rpc"
        try:
            with Client(stale_target, timeout=15):
                pass
        except Exception:
            report["stale_rpc_rejected"] = True
        else:
            raise RuntimeError("A stale resolved target connected to the replacement")
        _, report["replacement_rpc"] = smoke(args, second, target_file)
        report["stage"] = "complete"


def failure_diagnostics(args, *, scope_was_absent):
    """Inspect only this scope's full ID; never export Config, Env, or exec logs."""
    if not scope_was_absent:
        return {"worker": "scope_not_proven_empty"}
    result = {}
    try:
        worker = cli(args, "status", timeout=15)
        if worker is None:
            return {"worker": "absent"}
        identity = full_container_id(worker)
        result["container_id"] = identity
        rows = json.loads(engine(args, "container", "inspect", identity, timeout=15))
        if (
            not isinstance(rows, list)
            or len(rows) != 1
            or rows[0].get("Id") != identity
        ):
            raise RuntimeError("Failure inspection returned a different identity")
        state = rows[0].get("State", {})
        result["state"] = {
            "status": str(state.get("Status", ""))[:120],
            "error": str(state.get("Error", ""))[:4096],
        }
        # The provider's PID 1 is fixed sleep, not the setup exec or native daemon.
        result["pid1_logs_tail"] = engine(
            args, "logs", "--tail", "40", identity, timeout=15
        )[-4096:]
    except Exception as error:
        result["diagnostic_error"] = f"{type(error).__name__}: {error}"[:1000]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, choices=["docker", "podman"])
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"status": "failed", "stage": "starting", "engine": args.engine}
    started = time.monotonic()
    signal.signal(signal.SIGALRM, deadline)
    signal.signal(signal.SIGTERM, deadline)
    signal.alarm(1800)
    try:
        run(args, report)
        report["status"] = "passed"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        signal.alarm(0)
        if report["status"] != "passed":
            report["failure_diagnostics"] = failure_diagnostics(
                args, scope_was_absent=report.get("worker_scope_was_absent", False)
            )
        try:
            if report.get("worker_scope_was_absent", False):
                report["cleanup"] = cleanup(
                    ROOT, args.engine, args.endpoint, args.owner
                )
            else:
                report["cleanup"] = {"status": "skipped_unowned_scope"}
        except Exception as error:
            report["status"] = "failed"
            report["cleanup_error"] = f"{type(error).__name__}: {error}"
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
