#!/usr/bin/env python3
"""Operator-only disposable worker lifecycle. Not an Action Server action."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from worker_containers import ContainerProvider, WorkerError  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, choices=["docker", "podman"])
    parser.add_argument(
        "--endpoint", required=True, help="Explicit local unix:/// engine socket"
    )
    parser.add_argument("--owner", required=True)
    parser.add_argument("--worker", required=True)
    commands = parser.add_subparsers(dest="operation", required=True)
    create = commands.add_parser(
        "create", help="Create once; never replay uncertain creation"
    )
    create.add_argument("--source", type=Path, required=True)
    create.add_argument("--headroom-url", required=True)
    commands.add_parser("status")
    for name in ("start", "stop", "delete"):
        operation = commands.add_parser(name)
        operation.add_argument(
            "--container-id", required=True, help="Expected full ID from status"
        )
    args = parser.parse_args(argv)
    try:
        provider = ContainerProvider(
            args.engine, args.endpoint, args.owner, args.worker
        )
        if args.operation == "create":
            result = provider.create(args.source, args.headroom_url)
        elif args.operation == "status":
            result = provider.status()
        else:
            result = getattr(provider, args.operation)(args.container_id)
        print(
            json.dumps(asdict(result) if result is not None else None, sort_keys=True)
        )
        return 0
    except WorkerError as error:
        print(json.dumps({"error": str(error), "reconcile": "status"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
