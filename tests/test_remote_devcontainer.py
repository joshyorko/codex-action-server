"""Static contract for the native, self-contained Codex worker remote definition."""

import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
DEFINITION = ROOT / ".devcontainer/remote-worker/devcontainer.json"
SETUP = ROOT / "scripts/remote/setup.sh"
VERIFY = ROOT / "scripts/remote/verify.sh"
IMAGE = "ghcr.io/joshyorko/room-of-requirement:secure@sha256:de48bf9e2c93f8e358fcb5ac13e3159473245f5f271456c8f7d4dcc2ab881338"


def _artifacts() -> str:
    return "\n".join(path.read_text() for path in (DEFINITION, SETUP, VERIFY))


def test_definition_is_pinned_and_self_contained():
    definition = json.loads(DEFINITION.read_text())

    assert definition["image"] == IMAGE
    assert definition["remoteUser"] == "vscode"
    assert definition["waitFor"] == "postCreateCommand"
    assert isinstance(definition["postCreateCommand"], str)
    assert isinstance(definition["postStartCommand"], str)
    assert "scripts/remote/setup.sh" in definition["postCreateCommand"]
    assert "scripts/remote/verify.sh" in definition["postStartCommand"]
    assert "/workspaces/" not in definition["postCreateCommand"]


def test_expensive_setup_is_first_create_only_and_restart_is_cheap():
    definition = json.loads(DEFINITION.read_text())
    create = (definition["postCreateCommand"] + SETUP.read_text()).lower()
    start = (definition["postStartCommand"] + VERIFY.read_text()).lower()

    for marker in ("brew", "gh", "python", "headroom", "rtk", "codex", "luna-factory"):
        assert marker in create
    assert "brew install" not in start
    assert "brew tap" not in start
    assert "curl" not in start
    assert "plugin add" not in start
    assert "test -x" in start
    definition_data = json.loads(DEFINITION.read_text())
    start_command = definition_data["postStartCommand"]
    assert start_command.index("app-server daemon start") < start_command.index(
        "verify.sh"
    )
    assert "daemon version" in VERIFY.read_text()
    assert 'model = "gpt-6-luna"' not in VERIFY.read_text()


def test_poststart_propagates_selected_home_and_binary_to_daemon_and_verifier():
    start = json.loads(DEFINITION.read_text())["postStartCommand"]
    assert (
        'CODEX_HOME="${CODEX_WORKER_CODEX_HOME:-${CODEX_HOME:-/home/vscode/.codex}}"'
        in start
    )
    assert (
        'CODEX_BIN="${CODEX_WORKER_CODEX_BIN:-/home/vscode/.local/bin/codex}"' in start
    )
    assert (
        'CODEX_WORKER_CODEX_HOME="$CODEX_HOME" CODEX_WORKER_CODEX_BIN="$CODEX_BIN"'
        in start
    )
    assert '"$CODEX_BIN" app-server daemon start' in start
    assert "CODEX_WORKER_CODEX_CONFIG" in start


def test_worker_config_defaults_flow_through_setup_and_native_daemon_start():
    definition = json.loads(DEFINITION.read_text())
    start = definition["postStartCommand"]
    setup = SETUP.read_text()
    verify = VERIFY.read_text()

    assert "ensure_worker_defaults.py" in setup
    assert "export CODEX_HOME" in start
    assert '"$CODEX_BIN" app-server daemon start' in start
    assert "--sandbox" not in start
    assert "tomllib.load" in verify
    assert 'CONFIG="${CODEX_WORKER_CODEX_CONFIG:-$CODEX_HOME/config.toml}"' in verify


def test_scripts_are_independently_runnable_and_secret_safe():
    verify_text = VERIFY.read_text().lower()
    assert "requires_openai_auth" in verify_text
    assert "pluginid" in verify_text
    assert "installed" in verify_text
    assert "enabled" in verify_text

    for script in (SETUP, VERIFY):
        assert script.stat().st_mode & 0o111
        text = script.read_text().lower()
        assert "set -e" in text
        assert "device-auth" not in text
        assert "auth login" not in text
        assert "printenv" not in text
        assert "auth.json" not in text
        assert "access_token" not in text
        assert ".env" not in text


def test_remote_definition_has_no_controller_or_cluster_ownership():
    text = _artifacts().lower()

    for forbidden in (
        "kubectl",
        "action server",
        "remote_provision",
        "controller",
        "checkpoint",
        "workspace state",
        "task describe",
        "pty",
        "kubernetes",
        "pod discovery",
    ):
        assert forbidden not in text, forbidden


def test_old_action_server_definition_is_replaced():
    assert not (
        ROOT / "action-packages/codex-app-server/remote-devcontainer.json"
    ).exists()
