#!/usr/bin/env python3
"""Run the unchanged operator CLI with bounded diagnostics in secret-free CI only."""

from __future__ import annotations

import json
from pathlib import Path
import re
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_SENSITIVE = re.compile(
    r"authorization|bearer|password|secret|api[_ -]?key|access[_ -]?token|"
    r"refresh[_ -]?token|device[_ -]?(code|token)|user[_ -]?code|verification_uri|"
    r"login[_ -]?code|\b[A-Z0-9]{4}-[A-Z0-9]{4}\b",
    re.IGNORECASE,
)


def redact_output(output) -> str:
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    lines = str(output or "").splitlines()
    return "\n".join(
        "[redacted credential-related line]" if _SENSITIVE.search(line) else line
        for line in lines
    )[-1600:]


def command_stage(args) -> str:
    if args[0] != "exec":
        return "container_" + args[0]
    if any("scripts/remote/setup.sh" in part for part in args):
        return "recipe_create_hook"
    if any("app-server daemon start" in part for part in args):
        return "recipe_start_hook"
    if any("scripts/remote/verify.sh" in part for part in args):
        return "recipe_verify"
    return "worker_exec"


def trace_engine_run(original):
    def traced(self, args, **kwargs):
        from worker_containers import WorkerError

        try:
            return original(self, args, **kwargs)
        except WorkerError as error:
            # `raise ... from None` suppresses rendering, not the exception context.
            context = error.__context__
            diagnostic = {
                "diagnostic": "ci_engine_failure",
                "operation": args[0],
                "stage": command_stage(args),
            }
            if isinstance(context, subprocess.CalledProcessError):
                diagnostic["returncode"] = context.returncode
                diagnostic["stdout"] = redact_output(context.stdout)
                diagnostic["stderr"] = redact_output(context.stderr)
            elif isinstance(context, subprocess.TimeoutExpired):
                diagnostic["timeout_seconds"] = context.timeout
                diagnostic["stdout"] = redact_output(context.stdout)
                diagnostic["stderr"] = redact_output(context.stderr)
            print(json.dumps(diagnostic), file=sys.stderr, flush=True)
            raise

    return traced


def main():
    from worker_containers import ContainerEngine

    original = ContainerEngine.run
    ContainerEngine.run = trace_engine_run(original)
    try:
        # The real parser/main, provider commands, errors, and exit codes are unchanged.
        runpy.run_path(str(ROOT / "scripts/remote/worker.py"), run_name="__main__")
    finally:
        ContainerEngine.run = original


if __name__ == "__main__":
    main()
