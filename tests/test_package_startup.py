"""Offline launch sequencing against disposable runtime records."""

import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def launcher(tmp_path):
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    (root / "src/codex_shared").mkdir(parents=True)
    for name in [
        "run.sh",
        "run-packages.sh",
        "start_packages.py",
        "assemble_packages.py",
        "preflight.py",
    ]:
        path = ROOT / "scripts" / name
        if path.exists():
            shutil.copy(path, root / "scripts" / name)
    shutil.copy(ROOT / "package.yaml", root / "package.yaml")
    (root / "src/action_catalog_contract.py").write_text(
        "import os\n"
        "PACKAGE_ACTION_NAMES={'codex-action-server':frozenset({'read','write'}),"
        "'codex-observe':frozenset({'read'}),'codex-control':frozenset({'write'})}\n"
        "def selected_package_names():\n"
        " return tuple(os.environ.get('CODEX_ACTION_PACKAGES','codex-action-server').split(','))\n"
        "def action_names_for_deployment(profile=None,packages=None):\n"
        " names=frozenset().union(*(PACKAGE_ACTION_NAMES[p] for p in packages))\n"
        " return names & {'read'} if os.environ.get('CODEX_ACTION_PROFILE')=='observe' else names\n"
    )
    (root / "src/codex_shared/__init__.py").write_text("")
    (root / "src/codex_actions.py").write_text(
        "from capability_registration import action\n"
        "@action(package='codex-action-server')\n"
        "def read() -> str: return 'read'\n"
        "@action(package='codex-action-server')\n"
        "def write() -> str: return 'write'\n"
    )
    target = tmp_path / "targets.json"
    target.write_text('{"targets":{"local":{"transport":"local"}}}')
    binary = tmp_path / "action-server"
    binary.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sqlite3, sys
args=sys.argv[1:]
with open(os.environ['COMMAND_LOG'],'a') as f: f.write(json.dumps(args)+'\\n')
with open(os.environ['CWD_LOG'],'a') as f: f.write(str(pathlib.Path.cwd())+'\\n')
if args[0]=='import':
 directory=pathlib.Path(args[args.index('--dir')+1])
 package=next(line[6:] for line in (directory/'package.yaml').read_text().splitlines() if line.startswith('name: '))
 data=pathlib.Path(args[args.index('--datadir')+1])
 stored_directory=str(directory)
 if os.environ.get('FAKE_RELATIVE_PATHS'):
  relative=directory.relative_to(data)
  assert directory.samefile(relative)
  stored_directory=str(relative)
 with sqlite3.connect(data/'server.db') as db:
  db.execute('CREATE TABLE IF NOT EXISTS action_package(id TEXT, name TEXT, directory TEXT)')
  db.execute('CREATE TABLE IF NOT EXISTS action(action_package_id TEXT, name TEXT, enabled INTEGER, file TEXT)')
  db.execute('DELETE FROM action WHERE action_package_id=?',(package,))
  db.execute('DELETE FROM action_package WHERE id=?',(package,))
  db.execute('INSERT INTO action_package VALUES (?,?,?)',(package,package,stored_directory))
  name='read' if package=='codex-observe' else 'write'
  for i in range(2 if os.environ.get('FAKE_DUPLICATE') else 1):
   if os.environ.get('FAKE_MISSING')!=name:
    db.execute('INSERT INTO action VALUES (?,?,1,?)',(package,name,'src/codex_actions.py'))
""")
    binary.write_text(
        binary.read_text()
        + """
if args[0]=='start' and os.environ.get('FAKE_RELATIVE_PATHS'):
 data=pathlib.Path(args[args.index('--datadir')+1])
 with sqlite3.connect(data/'server.db') as db:
  for (directory,) in db.execute('SELECT directory FROM action_package'):
   assert pathlib.Path(directory).samefile(data/directory)
"""
    )
    binary.chmod(0o700)
    env = {
        **os.environ,
        "CODEX_ACTION_TARGETS": str(target),
        "CODEX_ACTION_RECEIPTS": str(tmp_path / "receipts"),
        "CODEX_ACTION_DATA": str(tmp_path / "runtime"),
        "ACTION_SERVER_BIN": str(binary),
        "COMMAND_LOG": str(tmp_path / "commands.jsonl"),
        "CWD_LOG": str(tmp_path / "working-directories.txt"),
    }
    env.pop("CODEX_ACTION_PACKAGE_ROOT", None)
    env.pop("CODEX_ACTION_PROFILE", None)
    env.pop("CODEX_ACTION_PACKAGES", None)
    return root, env


def run(launcher, **extra):
    root, env = launcher
    env = {**env, **extra}
    result = subprocess.run(
        ["bash", str(root / "scripts/run-packages.sh")],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )
    log = Path(env["COMMAND_LOG"])
    commands = (
        [json.loads(line) for line in log.read_text().splitlines()]
        if log.exists()
        else []
    )
    return result, commands


def test_compatibility_keeps_root_routes_and_one_process(launcher):
    result, commands = run(launcher)
    assert result.returncode == 0, result.stderr
    assert len(commands) == 1
    assert Path(launcher[1]["CWD_LOG"]).read_text().splitlines() == [str(Path.cwd())]
    start = commands[0]
    assert start[:3] == ["start", "--dir", str(launcher[0])]
    assert "--actions-sync=true" in start
    assert start[start.index("--address") + 1] == "127.0.0.1"
    assert (
        start[start.index("--whitelist") + 1]
        == "codex-action-server/read,codex-action-server/write"
    )
    assert start[start.index("--min-processes") + 1] == "1"
    assert start[start.index("--max-processes") + 1] == "1"


def test_split_imports_are_additive_and_start_exact_catalog(launcher):
    result, commands = run(
        launcher, CODEX_ACTION_PACKAGES="codex-observe,codex-control"
    )
    assert result.returncode == 0, result.stderr
    assert [command[0] for command in commands] == ["import", "import", "start"]
    assert len({command[command.index("--datadir") + 1] for command in commands}) == 1
    start = commands[-1]
    assert "--actions-sync=false" in start
    assert (
        start[start.index("--whitelist") + 1]
        == "codex-observe/read,codex-control/write"
    )
    for command, package in zip(commands, ["codex-observe", "codex-control"]):
        directory = Path(command[command.index("--dir") + 1])
        assert directory.name == package
        assert (directory / "src/codex_shared/__init__.py").is_file()


def test_reused_datadir_selection_and_profile_exclude_retained_mutations(launcher):
    result, _ = run(launcher, CODEX_ACTION_PACKAGES="codex-observe,codex-control")
    assert result.returncode == 0, result.stderr
    result, commands = run(
        launcher, CODEX_ACTION_PACKAGES="codex-observe", CODEX_ACTION_PROFILE="observe"
    )
    assert result.returncode == 0, result.stderr
    assert commands[-1][commands[-1].index("--whitelist") + 1] == "codex-observe/read"
    with sqlite3.connect(Path(launcher[1]["CODEX_ACTION_DATA"]) / "server.db") as db:
        assert (
            db.execute("SELECT COUNT(*) FROM action WHERE name='write'").fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("extra", [{"FAKE_DUPLICATE": "1"}, {"FAKE_MISSING": "read"}])
def test_incomplete_or_duplicate_catalog_fails_before_start(launcher, extra):
    result, commands = run(launcher, CODEX_ACTION_PACKAGES="codex-observe", **extra)
    assert result.returncode != 0
    assert [command[0] for command in commands] == ["import"]


def test_missing_explicit_artifacts_fail_without_runtime_import(launcher, tmp_path):
    result, commands = run(
        launcher,
        CODEX_ACTION_PACKAGES="codex-observe",
        CODEX_ACTION_PACKAGE_ROOT=str(tmp_path / "missing"),
    )
    assert result.returncode != 0
    assert commands == []


def test_datadir_relative_package_records_resolve_on_import_start_and_restart(launcher):
    for _ in range(2):
        result, commands = run(
            launcher,
            CODEX_ACTION_PACKAGES="codex-observe,codex-control",
            FAKE_RELATIVE_PATHS="1",
        )
        assert result.returncode == 0, result.stderr
    data = Path(launcher[1]["CODEX_ACTION_DATA"])
    assert len(commands) == 6
    assert Path(launcher[1]["CWD_LOG"]).read_text().splitlines() == [str(data)] * 6
    with sqlite3.connect(data / "server.db") as db:
        directories = db.execute("SELECT directory FROM action_package").fetchall()
    assert len(directories) == 2
    for (directory,) in directories:
        assert not Path(directory).is_absolute()
        assert (data / directory / "src/codex_actions.py").is_file()
