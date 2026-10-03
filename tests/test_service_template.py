"""Static unit validation never installs or starts a service."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


def test_user_service_template_parses_without_activation(tmp_path):
    binary = shutil.which("systemd-analyze")
    if not binary:
        pytest.skip("systemd-analyze unavailable")
    unit = Path(__file__).parents[1] / "deploy/codex-action-server.service"
    result = subprocess.run(
        [binary, "--user", "verify", str(unit)],
        env={**os.environ, "XDG_RUNTIME_DIR": str(tmp_path)},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
