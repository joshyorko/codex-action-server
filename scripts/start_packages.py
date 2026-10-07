#!/usr/bin/env python3
"""Launch one Runtime with exact source-owned package/action selection."""

import argparse
import os
from pathlib import Path
import runpy
import sqlite3
import subprocess

from assemble_packages import assemble, source_fingerprint, verify_artifacts

ROOT = Path(__file__).resolve().parents[1]


def assert_imported_catalog(
    data: Path, expected: dict[str, frozenset[str]], directories: dict[str, Path]
) -> None:
    """Read the effective whitelist projection without modifying retained records."""
    database = data / "server.db"
    if database.is_symlink() or not database.is_file():
        raise ValueError("imported_catalog_database_required")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT p.name, a.name, a.file, p.directory FROM action a "
            "JOIN action_package p ON p.id = a.action_package_id WHERE a.enabled = 1"
        ).fetchall()
    actual = []
    for package, name, file, directory in rows:
        # This is the same exact selection passed to the Runtime whitelist.
        # Unselected retained records remain in storage but cannot become routes.
        if package not in expected or name not in expected[package]:
            continue
        location = Path(directory)
        if not location.is_absolute():
            location = data / location
        if (
            file != "src/codex_actions.py"
            or location.resolve() != directories[package].resolve()
        ):
            raise ValueError("imported_catalog_entrypoint_mismatch")
        actual.append((package, name))
    wanted = {(package, name) for package, names in expected.items() for name in names}
    if set(actual) != wanted or len(actual) != len(wanted):
        raise ValueError("imported_catalog_missing_or_duplicate_actions")


def start(address: str, port: str) -> None:
    registry = runpy.run_path(str(ROOT / "src/action_catalog_contract.py"))
    packages = registry["selected_package_names"]()
    allowed = registry["action_names_for_deployment"](packages=packages)
    expected = {
        package: registry["PACKAGE_ACTION_NAMES"][package] & allowed
        for package in packages
    }
    if not all(expected.values()):
        raise ValueError("empty_selected_package_catalog")
    whitelist = ",".join(
        f"{package}/{name}"
        for package, names in expected.items()
        for name in sorted(names)
    )
    data = Path(os.environ["CODEX_ACTION_DATA"])
    binary = os.environ.get("ACTION_SERVER_BIN", "action-server")
    split = packages != ("codex-action-server",)
    if split:
        # Core and its log rewrite hook must not mutate verified source artifacts.
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        configured = os.environ.get("CODEX_ACTION_PACKAGE_ROOT")
        if configured is not None:
            artifacts = Path(configured)
            if not artifacts.is_absolute():
                raise ValueError("absolute_package_root_required")
            verify_artifacts(artifacts, source_fingerprint(ROOT))
        elif (ROOT / "packages").exists():
            artifacts = ROOT / "packages"
            verify_artifacts(artifacts, source_fingerprint(ROOT))
        else:
            artifacts = data / "packages" / source_fingerprint(ROOT)
            assemble(ROOT, artifacts)
        directories = {package: artifacts / package for package in packages}
        for package in packages:
            subprocess.run(
                [
                    binary,
                    "import",
                    "--dir",
                    str(directories[package]),
                    "--datadir",
                    str(data),
                    "--whitelist",
                    whitelist,
                ],
                check=True,
                cwd=data,
            )
        assert_imported_catalog(data, expected, directories)
        directory = directories[packages[0]]
        # Published Runtime resolves datadir-relative package records from cwd.
        os.chdir(data)
    else:
        directory = ROOT
    command = [
        binary,
        "start",
        "--dir",
        str(directory),
        "--datadir",
        str(data),
        "--address",
        address,
        "--port",
        port,
        f"--actions-sync={'false' if split else 'true'}",
        "--whitelist",
        whitelist,
        "--min-processes",
        "1",
        "--max-processes",
        "1",
    ]
    os.execvp(binary, command)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True)
    parser.add_argument("--port", required=True)
    args = parser.parse_args()
    try:
        start(args.address, args.port)
    except (
        OSError,
        ValueError,
        KeyError,
        sqlite3.Error,
        subprocess.CalledProcessError,
    ) as exc:
        parser.exit(2, f"Package startup failed: {exc}\n")


if __name__ == "__main__":
    main()
