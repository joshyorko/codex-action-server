"""Fresh worker setup keeps its auth policy after Headroom config generation."""

from pathlib import Path
import subprocess
import sys
import tomllib

import pytest


SETUP = Path(__file__).resolve().parents[1] / "scripts/remote/setup.sh"


@pytest.fixture
def worker_setup(tmp_path):
    tools = tmp_path / "bin"
    tools.mkdir()
    for name in ("brew", "gh", "rtk", "codex"):
        executable = tools / name
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
    headroom = tools / "headroom"
    # Headroom cb528b994695 removes its provider table and regenerates it
    # without requires_openai_auth for a fresh, unauthenticated Codex home.
    headroom.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "from pathlib import Path\n"
        "home = Path(os.environ['CODEX_HOME'])\n"
        "path = home / 'config.toml'\n"
        "policy = os.environ.get('TEST_HEADROOM_POLICY', '')\n"
        "content = path.read_text().split('[model_providers.headroom]')[0]\n"
        "path.write_text(content + 'model_provider = \"headroom\"\\n'"
        " + '[model_providers.headroom]\\n'"
        " + 'name = \"OpenAI via Headroom proxy\"\\n'"
        " + 'base_url = \"http://127.0.0.1:9/v1\"\\n'"
        " + policy + 'supports_websockets = true\\n'"
        " + '[model_providers.other]\\nrequires_openai_auth = false\\n')\n"
    )
    headroom.chmod(0o755)
    home = tmp_path / "codex-home"
    config = home / "config.toml"
    env = {
        "PATH": f"{tools}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "CODEX_HOME": str(home),
        "CODEX_WORKER_BREW": str(tools / "brew"),
        "CODEX_WORKER_GH_BIN": str(tools / "gh"),
        "CODEX_WORKER_PYTHON": sys.executable,
        "CODEX_WORKER_HEADROOM_BIN": str(headroom),
        "CODEX_WORKER_RTK_BIN": str(tools / "rtk"),
        "CODEX_WORKER_CODEX_BIN": str(tools / "codex"),
        "CODEX_WORKER_HEADROOM_URL": "http://127.0.0.1:9/v1",
    }
    return config, env


@pytest.mark.parametrize("policy", ["", "requires_openai_auth = false\n"])
def test_fresh_headroom_generation_preserves_worker_auth_policy(worker_setup, policy):
    config, env = worker_setup
    env["TEST_HEADROOM_POLICY"] = policy
    result = subprocess.run(
        ["bash", str(SETUP)], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    parsed = tomllib.loads(config.read_text())
    assert parsed["model_provider"] == "headroom"
    assert parsed["model_providers"]["headroom"]["requires_openai_auth"] is True
    assert parsed["model_providers"]["headroom"]["supports_websockets"] is True
    assert parsed["model_providers"]["headroom"]["base_url"] == "http://127.0.0.1:9/v1"
    assert parsed["model_providers"]["other"]["requires_openai_auth"] is False
    assert parsed["model"] == "gpt-6-luna"


def test_retained_headroom_policy_is_not_rewritten(worker_setup):
    config, env = worker_setup
    config.parent.mkdir()
    original = (
        'model_provider = "headroom"\n'
        "[model_providers.headroom]\n"
        'base_url = "http://operator.example/v1"\n'
        "requires_openai_auth = false\n"
    )
    config.write_text(original)
    result = subprocess.run(
        ["bash", str(SETUP)], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    parsed = tomllib.loads(config.read_text())
    assert parsed["model_provider"] == "headroom"
    assert parsed["model_providers"]["headroom"]["base_url"] == (
        "http://operator.example/v1"
    )
    assert parsed["model_providers"]["headroom"]["requires_openai_auth"] is False
    assert parsed["sandbox_mode"] == "workspace-write"
