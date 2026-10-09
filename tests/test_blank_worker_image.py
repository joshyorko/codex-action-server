"""The worker runtime is separate from every project checkout."""

import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def metadata():
    label = next(
        line
        for line in (ROOT / "Containerfile.worker").read_text().splitlines()
        if line.startswith("LABEL devcontainer.metadata=")
    )
    return json.loads(json.loads(label.split("=", 1)[1]))[0]


def test_worker_metadata_reuses_bootstrap_without_source_checkout():
    original = json.loads(
        (ROOT / ".devcontainer/remote-worker/devcontainer.json").read_text()
    )
    worker = metadata()
    assert worker["workspaceFolder"] == "/workspaces"
    assert worker["remoteUser"] == "vscode"
    assert worker["waitFor"] == "postCreateCommand"
    for hook in ("postCreateCommand",):
        assert worker[hook] == original[hook].replace(
            "scripts/remote/", "/opt/codex-worker/"
        )
    copies = [
        line
        for line in (ROOT / "Containerfile.worker").read_text().splitlines()
        if line.startswith("COPY ")
    ]
    assert copies == [
        "COPY --chmod=0755 scripts/remote/setup.sh scripts/remote/verify.sh scripts/remote/start.sh /opt/codex-worker/"
    ]


def test_built_worker_is_blank_and_contains_exact_bootstrap():
    image = os.environ.get("CODEX_WORKER_TEST_IMAGE")
    if not image:
        pytest.skip("set CODEX_WORKER_TEST_IMAGE for built-image acceptance")
    info = json.loads(subprocess.check_output(["docker", "image", "inspect", image]))[
        0
    ]["Config"]
    assert info["WorkingDir"] == "/workspaces"
    assert info["User"] == "vscode"
    assert json.loads(info["Labels"]["devcontainer.metadata"])[0] == metadata()
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--entrypoint",
            "/bin/bash",
            image,
            "-c",
            'set -eu; test -z "$(find /workspaces -mindepth 1 -print -quit)"; test -w /workspaces; test ! -d /opt/codex-worker/.git; bash -n /opt/codex-worker/setup.sh /opt/codex-worker/verify.sh',
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    for name in ("setup.sh", "verify.sh", "start.sh"):
        actual = subprocess.check_output(
            [
                "docker",
                "run",
                "--rm",
                "--network=none",
                "--entrypoint",
                "/bin/cat",
                image,
                f"/opt/codex-worker/{name}",
            ]
        )
        assert actual == (ROOT / "scripts/remote" / name).read_bytes()


def test_worker_publication_cannot_replace_api_image_tags():
    workflow = (ROOT / ".github/workflows/worker-image.yml").read_text()
    assert 'destination="$image:worker-sha-$GITHUB_SHA"' in workflow
    assert '"$image:worker-latest"' in workflow
    assert '"$image:latest"' not in workflow
    assert "git ls-remote origin refs/heads/main" in workflow
    assert workflow.index("Verify blank image") < workflow.index(
        "Publish verified main"
    )


def test_start_script_preserves_runtime_environment():
    worker = metadata()
    assert worker["postStartCommand"] == "/bin/bash /opt/codex-worker/start.sh"
    assert "$" not in json.dumps(worker)
    original = json.loads(
        (ROOT / ".devcontainer/remote-worker/devcontainer.json").read_text()
    )
    assert (
        original["postStartCommand"].replace("scripts/remote/", "/opt/codex-worker/")
        in (ROOT / "scripts/remote/start.sh").read_text()
    )


def test_start_uses_runtime_home_and_binary_and_refuses_config_escape(tmp_path):
    binary = tmp_path / "codex"
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$CODEX_HOME" "$*" > "$PROBE"\n')
    binary.chmod(0o755)
    verifier = tmp_path / "verify"
    verifier.write_text(
        '#!/bin/sh\ntest "$CODEX_WORKER_CODEX_HOME" = "$CODEX_HOME"\ntest "$CODEX_WORKER_CODEX_BIN" = "$EXPECTED_BINARY"\n'
    )
    verifier.chmod(0o755)
    script = tmp_path / "start.sh"
    script.write_text(
        (ROOT / "scripts/remote/start.sh")
        .read_text()
        .replace("/opt/codex-worker/verify.sh", str(verifier))
    )
    home = str(tmp_path / "selected-home")
    probe = tmp_path / "probe"
    env = {
        **os.environ,
        "CODEX_WORKER_CODEX_HOME": home,
        "CODEX_WORKER_CODEX_BIN": str(binary),
        "CODEX_WORKER_CODEX_CONFIG": home + "/config.toml",
        "PROBE": str(probe),
        "EXPECTED_BINARY": str(binary),
    }
    result = subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert probe.read_text().splitlines() == [home, "app-server daemon start"]
    probe.unlink()
    env["CODEX_WORKER_CODEX_CONFIG"] = str(tmp_path / "elsewhere")
    result = subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True
    )
    assert result.returncode == 2
    assert not probe.exists()
