#!/usr/bin/env python3
"""Offline startup checks. Never connects to Codex or resolves remote targets."""

import json
import os
from pathlib import Path
import re
import sys
import tempfile


def main():
    os.umask(0o077)
    port = os.environ.get("CODEX_ACTION_PORT", "8088")
    if not re.fullmatch(r"[0-9]{1,5}", port) or not 1024 <= int(port) <= 65535:
        raise ValueError("invalid_unprivileged_port")
    target = Path(os.environ["CODEX_ACTION_TARGETS"])
    if not target.is_absolute():
        raise ValueError("absolute_target_path_required")
    data = json.loads(target.read_text())
    if not isinstance(data, dict) or set(data) != {"targets"}:
        raise ValueError("invalid_target_configuration")
    targets = data["targets"]
    if not isinstance(targets, dict) or not targets:
        raise ValueError("invalid_target_configuration")
    for name, config in targets.items():
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name)
            or not isinstance(config, dict)
            or config.get("transport")
            not in {"local", "ssh", "devsy", "devsy-kubernetes", "container"}
        ):
            raise ValueError("invalid_target_configuration")
        if config.get("transport") == "ssh":
            ssh_config = config.get("ssh_config")
            known_hosts = config.get("known_hosts")
            if (ssh_config is None) != (known_hosts is None):
                raise ValueError("incomplete_pinned_ssh_configuration")
            if ssh_config is not None:
                for path in (ssh_config, known_hosts):
                    candidate = Path(path)
                    if (
                        not candidate.is_absolute()
                        or ".." in candidate.parts
                        or candidate.is_symlink()
                        or not candidate.is_file()
                        or not os.access(candidate, os.R_OK)
                    ):
                        raise ValueError("invalid_pinned_ssh_file")
        elif "ssh_config" in config or "known_hosts" in config:
            raise ValueError("pinned_ssh_configuration_requires_ssh_transport")
    paths = [
        Path(os.environ[key]) for key in ["CODEX_ACTION_RECEIPTS", "CODEX_ACTION_DATA"]
    ]
    if (
        any(not path.is_absolute() for path in paths)
        or paths[0].resolve() == paths[1].resolve()
    ):
        raise ValueError("distinct_absolute_state_paths_required")
    for path in paths:
        # Never chmod an existing operator directory or follow its final symlink.
        if path.is_symlink():
            raise ValueError("state_directory_symlink")
        created = []
        ancestor = path
        while not ancestor.exists():
            created.append(ancestor)
            ancestor = ancestor.parent
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        stat = path.stat()
        if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
            raise ValueError("state_directory_must_be_private_and_owned")
        # Exercise write + fsync without touching existing receipt contents.
        with tempfile.TemporaryFile(dir=path) as probe:
            probe.write(b"preflight")
            probe.flush()
            os.fsync(probe.fileno())
        # Sync new directory entries, without requiring reads of unrelated ancestors.
        directories = [path, *[entry.parent for entry in created]]
        for directory in dict.fromkeys(directories):
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    print(int(port))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError):
        print(
            "Control API preflight failed: check target JSON, port, and private writable state directories",
            file=sys.stderr,
        )
        sys.exit(2)
