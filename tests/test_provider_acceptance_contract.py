"""Offline contract checks. These do not count as live provider acceptance."""

import importlib.util
from pathlib import Path
import socket
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_driver():
    path = ROOT / "ci" / "dagger_acceptance.py"
    assert path.is_file(), "The portable Dagger acceptance driver is missing"
    spec = importlib.util.spec_from_file_location("dagger_acceptance", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


@pytest.fixture
def source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    git(source, "config", "user.email", "acceptance@example.invalid")
    git(source, "config", "user.name", "Acceptance test")
    (source / "tracked.txt").write_text("original\n")
    git(source, "add", "tracked.txt")
    git(source, "commit", "-qm", "Source fixture")
    return source


def test_source_snapshot_preserves_real_commit_without_git_credentials(
    source, tmp_path
):
    driver = load_driver()
    git(source, "config", "http.https://example.invalid/.extraheader", "secret-fixture")
    snapshot = tmp_path / "snapshot"
    actual = driver.prepare_source(source, snapshot)
    assert actual == git(source, "rev-parse", "HEAD")
    assert git(snapshot, "rev-parse", "HEAD") == actual
    assert (snapshot / "tracked.txt").read_text() == "original\n"
    assert "secret-fixture" not in (snapshot / ".git/config").read_text()
    assert git(snapshot, "status", "--porcelain") == ""


def test_source_snapshot_rejects_uncommitted_input(source, tmp_path):
    driver = load_driver()
    (source / "tracked.txt").write_text("changed\n")
    with pytest.raises(ValueError, match="clean committed"):
        driver.prepare_source(source, tmp_path / "snapshot")


def test_socket_requires_explicit_existing_unix_socket(tmp_path):
    driver = load_driver()
    path = tmp_path / "engine.sock"
    try:
        sock = socket.socket(socket.AF_UNIX)
    except PermissionError:
        pytest.skip("This execution sandbox disallows Unix sockets")
    with sock:
        sock.bind(str(path))
        assert driver.validate_socket(str(path)) == path
    regular = tmp_path / "regular"
    regular.write_text("no")
    with pytest.raises(ValueError, match="Unix socket"):
        driver.validate_socket(str(regular))
    with pytest.raises(ValueError, match="absolute"):
        driver.validate_socket("relative.sock")


def test_cleanup_rejects_short_or_wrong_container_identity():
    driver = load_driver()
    assert driver.full_container_id({"container_id": "a" * 64}) == "a" * 64
    for value in (None, {}, {"container_id": "a" * 12}, {"container_id": "-all"}):
        with pytest.raises(ValueError, match="full container"):
            driver.full_container_id(value)


def test_workflow_runs_both_engines_without_repository_credentials():
    workflow = ROOT / ".github/workflows/provider-acceptance.yml"
    assert workflow.is_file(), "A separate real-provider workflow is missing"
    content = workflow.read_text()
    assert "engine: [docker, podman]" in content
    assert "persist-credentials: false" in content
    assert "pull_request_target" not in content
    assert "v0.21.10" in content
    assert "DAGGER_CLOUD_TOKEN" not in content


def test_cleanup_stops_running_worker_before_exact_delete(tmp_path):
    driver = load_driver()
    script = tmp_path / "scripts/remote/worker.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "import json, pathlib, sys\n"
        "root = pathlib.Path(__file__).parents[2]\n"
        "state = root / 'state.json'\n"
        "log = root / 'operations.jsonl'\n"
        "operation = sys.argv[sys.argv.index('--worker') + 2:]\n"
        "with log.open('a') as f: f.write(json.dumps(operation) + '\\n')\n"
        "worker = json.loads(state.read_text())\n"
        "if operation[0] == 'stop':\n"
        "    assert operation == ['stop', '--container-id', worker['container_id']]\n"
        "    worker['running'] = False\n"
        "elif operation[0] == 'delete':\n"
        "    assert not worker['running']\n"
        "    assert operation == ['delete', '--container-id', worker['container_id']]\n"
        "    worker = None\n"
        "state.write_text(json.dumps(worker))\n"
        "print(json.dumps(worker))\n"
    )
    import json

    identity = "a" * 64
    (tmp_path / "state.json").write_text(
        json.dumps({"container_id": identity, "running": True})
    )
    result = driver.cleanup(tmp_path, "docker", "unix:///test.sock", "test-run")
    assert result == {"status": "deleted", "container_id": identity}
    operations = [
        json.loads(line)
        for line in (tmp_path / "operations.jsonl").read_text().splitlines()
    ]
    assert operations == [
        ["status"],
        ["stop", "--container-id", identity],
        ["delete", "--container-id", identity],
        ["status"],
    ]


def test_workflow_job_env_uses_only_available_github_contexts():
    import re

    content = (ROOT / ".github/workflows/provider-acceptance.yml").read_text()
    job_env = content.split("    env:\n", 1)[1].split("    steps:\n", 1)[0]
    contexts = set(re.findall(r"\$\{\{\s*([a-zA-Z_]+)\.", job_env))
    # GitHub's contexts reference excludes runner at jobs.<job_id>.env.
    assert contexts <= {
        "github",
        "needs",
        "strategy",
        "matrix",
        "vars",
        "secrets",
        "inputs",
    }
    assert "ACCEPTANCE_OUTPUT=$RUNNER_TEMP/" in content


def test_driver_rejects_owner_longer_than_provider_limit():
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "ci/dagger_acceptance.py"),
            "--engine",
            "docker",
            "--socket",
            "/unused.sock",
            "--owner",
            "a" * 65,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "owner must be a unique run-scoped identifier" in result.stderr
