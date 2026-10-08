"""Exercise the real launcher without connecting to native Codex."""

import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def launch(tmp_path, config=None, port="8088", receipt_file=False):
    target = tmp_path / "targets.json"
    target.write_text(
        json.dumps(config or {"targets": {"local": {"transport": "local"}}})
    )
    receipts = tmp_path / "receipts"
    if receipt_file:
        receipts.write_text("not a directory")
    env = {
        **os.environ,
        "ACTION_SERVER_BIN": "/bin/true",
        "CODEX_ACTION_TARGETS": str(target),
        "CODEX_ACTION_RECEIPTS": str(receipts),
        "CODEX_ACTION_DATA": str(tmp_path / "runtime"),
        "CODEX_ACTION_PORT": port,
    }
    return subprocess.run(
        ["bash", str(ROOT / "scripts/run.sh")], env=env, text=True, capture_output=True
    )


def test_launch_rejects_invalid_target_shape(tmp_path):
    assert launch(tmp_path, {"other": "wrong"}).returncode != 0


def test_launch_rejects_receipt_file(tmp_path):
    assert launch(tmp_path, receipt_file=True).returncode != 0


@pytest.mark.parametrize("port", ["18446744073709559704", "0", "65536", "1+8087"])
def test_launch_rejects_invalid_port(tmp_path, port):
    assert launch(tmp_path, port=port).returncode != 0


def test_launch_accepts_decimal_leading_zero(tmp_path):
    assert launch(tmp_path, port="08088").returncode == 0


def test_launch_creates_private_state(tmp_path):
    assert launch(tmp_path).returncode == 0
    for name in ["runtime", "receipts"]:
        assert (tmp_path / name).is_dir()
        assert (tmp_path / name).stat().st_mode & 0o077 == 0


def test_launch_rejects_world_readable_receipts(tmp_path):
    (tmp_path / "receipts").mkdir(mode=0o755)
    assert launch(tmp_path).returncode != 0


def test_launch_rejects_unknown_transport(tmp_path):
    assert launch(tmp_path, {"targets": {"bad": {"transport": "exec"}}}).returncode != 0


def test_existing_private_state_under_traversable_ancestor(tmp_path):
    parent = tmp_path / "traverse-only"
    parent.mkdir()
    root = parent / "owned"
    root.mkdir(mode=0o700)
    for name in ["runtime", "receipts"]:
        (root / name).mkdir(mode=0o700)
    parent.chmod(0o111)
    try:
        assert launch(root).returncode == 0
    finally:
        parent.chmod(0o700)


def test_direct_preflight_creates_private_ancestors(tmp_path):
    import sys

    target = tmp_path / "targets.json"
    target.write_text(json.dumps({"targets": {"local": {"transport": "local"}}}))
    env = {
        **os.environ,
        "CODEX_ACTION_TARGETS": str(target),
        "CODEX_ACTION_RECEIPTS": str(tmp_path / "new/receipts"),
        "CODEX_ACTION_DATA": str(tmp_path / "new/runtime"),
    }
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/preflight.py")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "new").stat().st_mode & 0o077 == 0


def test_preflight_accepts_pinned_devsy_kubernetes_target_offline(
    tmp_path, monkeypatch, capsys
):
    import runpy

    target = tmp_path / "targets.json"
    target.write_text(
        json.dumps(
            {
                "targets": {
                    "devsy": {
                        "transport": "devsy-kubernetes",
                        "context": "default",
                        "provider": "kubernetes",
                        "workspace": "worker",
                        "workspace_uid": "uid-1",
                        "user": "vscode",
                    }
                }
            }
        )
    )
    for key, value in {
        "CODEX_ACTION_TARGETS": target,
        "CODEX_ACTION_RECEIPTS": tmp_path / "receipts",
        "CODEX_ACTION_DATA": tmp_path / "runtime",
        "CODEX_ACTION_PORT": "8088",
    }.items():
        monkeypatch.setenv(key, str(value))

    def reject_process(*args, **kwargs):
        pytest.fail("Offline preflight must not launch remote processes")

    monkeypatch.setattr(subprocess, "Popen", reject_process)
    preflight = runpy.run_path(str(ROOT / "scripts/preflight.py"))
    previous_umask = os.umask(0o077)
    try:
        preflight["main"]()
    finally:
        os.umask(previous_umask)
    assert capsys.readouterr().out == "8088\n"
    for name in ["runtime", "receipts"]:
        assert (tmp_path / name).is_dir()
        assert (tmp_path / name).stat().st_mode & 0o077 == 0
