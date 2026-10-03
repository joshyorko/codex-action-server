from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "scripts/remote/verify.sh"


class RemoteVerifyRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self._command("gh", "raise SystemExit(0)")
        self._command("headroom", "raise SystemExit(0)")
        self._command("rtk", "raise SystemExit(0)")
        self._command(
            "python",
            f"import os,sys; error=os.environ.get('CODEX_WORKER_TEST_PYTHON_ERROR'); print(error, file=sys.stderr) if error else None; os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]]) if not error else sys.exit(2)",
        )
        self._command(
            "codex",
            """import json, os, sys
args = sys.argv[1:]
calls = os.environ.get('CODEX_WORKER_TEST_NATIVE_CALLS')
if calls:
    with open(calls, 'a') as log:
        log.write(' '.join(args) + '\\n')
expected_home = os.environ.get('CODEX_WORKER_TEST_EXPECTED_CODEX_HOME')
if expected_home and os.environ.get('CODEX_HOME') != expected_home:
    print('wrong Codex home', file=sys.stderr)
    raise SystemExit(21)
if args == ['--version']:
    print('codex test-cli')
elif args == ['app-server', 'daemon', 'version']:
    print(json.dumps({'status': os.environ['CODEX_WORKER_TEST_DAEMON_STATUS'], 'socketPath': '/tmp/test.sock'}))
elif args == ['plugin', 'list', '--marketplace', 'plugins', '--json']:
    print(json.dumps({'installed': [{'pluginId': 'luna-factory@plugins', 'installed': True, 'enabled': True}]}))
else:
    raise SystemExit(2)
""",
        )
        self.codex_home = self.root / ".codex"
        self.codex_home.mkdir()
        config = self.codex_home / "config.toml"
        config.write_text(
            'model = "operator-model"\n'
            'model_provider = "operator-provider"\n'
            'model_reasoning_effort = "low"\n'
            "\n[model_providers.operator-provider]\n"
            'base_url = "http://provider.example/v1"\n'
            "requires_openai_auth = true\n"
        )
        self.env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "CODEX_WORKER_CODEX_CONFIG": str(config),
            "CODEX_WORKER_CODEX_HOME": str(self.codex_home),
            "CODEX_WORKER_CODEX_BIN": str(self.bin / "codex"),
            "CODEX_WORKER_GH_BIN": str(self.bin / "gh"),
            "CODEX_WORKER_HEADROOM_BIN": str(self.bin / "headroom"),
            "CODEX_WORKER_RTK_BIN": str(self.bin / "rtk"),
            "CODEX_WORKER_PYTHON": str(self.bin / "python"),
            "CODEX_WORKER_TEST_DAEMON_STATUS": "running",
        }

    def _command(self, name, body):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n{body}\n")
        path.chmod(0o755)

    def run_verify(self):
        return subprocess.run(
            ["bash", str(VERIFY)], env=self.env, text=True, capture_output=True
        )

    def test_verify_accepts_retained_model_and_provider_with_ready_daemon(self):
        result = self.run_verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("codex app server daemon ready", result.stdout.lower())

    def test_verify_accepts_retained_native_default_provider(self):
        config = Path(self.env["CODEX_WORKER_CODEX_CONFIG"])
        config.write_text('model = "operator-model"\nmodel_reasoning_effort = "low"\n')
        result = self.run_verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_verify_rejects_config_outside_selected_codex_home_before_tools(self):
        calls = self.root / "native-calls"
        self.env["CODEX_WORKER_TEST_NATIVE_CALLS"] = str(calls)
        self.env["CODEX_WORKER_CODEX_CONFIG"] = str(self.root / "elsewhere.toml")
        result = self.run_verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Codex config path", (result.stdout + result.stderr))
        self.assertFalse(calls.exists())

    def test_verify_uses_inherited_native_codex_home(self):
        self.env.pop("CODEX_WORKER_CODEX_HOME")
        self.env.pop("CODEX_WORKER_CODEX_CONFIG")
        self.env["CODEX_HOME"] = str(self.codex_home)
        self.env["CODEX_WORKER_TEST_EXPECTED_CODEX_HOME"] = str(self.codex_home)
        result = self.run_verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_verify_uses_explicit_friday_codex_home(self):
        self.env.pop("CODEX_HOME", None)
        self.env.pop("CODEX_WORKER_CODEX_CONFIG")
        self.env["CODEX_WORKER_TEST_EXPECTED_CODEX_HOME"] = str(self.codex_home)
        result = self.run_verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_verify_fails_when_daemon_is_not_running(self):
        self.env["CODEX_WORKER_TEST_DAEMON_STATUS"] = "stopped"
        result = self.run_verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("daemon", (result.stdout + result.stderr).lower())

    def test_verify_rejects_headroom_without_required_auth(self):
        config = Path(self.env["CODEX_WORKER_CODEX_CONFIG"])
        config.write_text(
            'model_provider = "headroom"\n'
            "\n[model_providers.headroom]\n"
            'base_url = "http://headroom.example/v1"\n'
            "requires_openai_auth = false\n"
        )
        result = self.run_verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "codex configuration validation failed",
            (result.stdout + result.stderr).lower(),
        )
        self.assertIn("codex login", result.stdout)

    def test_verify_never_prints_native_daemon_stderr(self):
        sentinel = "synthetic-private-daemon-5b1e"
        codex = Path(self.env["CODEX_WORKER_CODEX_BIN"])
        codex.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            "if sys.argv[1:] == ['--version']:\n print('codex test-cli')\n"
            f"elif sys.argv[1:] == ['app-server', 'daemon', 'version']:\n print({sentinel!r}, file=sys.stderr); raise SystemExit(23)\n"
        )
        codex.chmod(0o755)
        result = self.run_verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("daemon", (result.stdout + result.stderr).lower())
        self.assertNotIn(sentinel, result.stdout + result.stderr)

    def test_verify_never_prints_config_parser_stderr(self):
        sentinel = "synthetic-private-config-31a7"
        self.env["CODEX_WORKER_TEST_PYTHON_ERROR"] = sentinel
        result = self.run_verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("config", (result.stdout + result.stderr).lower())
        self.assertNotIn(sentinel, result.stdout + result.stderr)

    def test_verify_never_prints_other_native_cli_stderr(self):
        sentinel = "synthetic-private-cli-884e"
        failing_commands = {
            "headroom": f"import sys; print({sentinel!r}, file=sys.stderr); raise SystemExit(3)",
            "rtk": f"import sys; print({sentinel!r}, file=sys.stderr); raise SystemExit(3)",
            "codex": f"""import json,sys
args=sys.argv[1:]
if args == ['--version']:
    print('codex test-cli')
elif args == ['app-server','daemon','version']:
    print(json.dumps({{'status':'running','socketPath':'/tmp/test.sock'}}))
elif args == ['plugin','list','--marketplace','plugins','--json']:
    print({sentinel!r}, file=sys.stderr)
    raise SystemExit(3)
""",
        }
        for name, source in failing_commands.items():
            with self.subTest(name=name):
                command = Path(self.env[f"CODEX_WORKER_{name.upper()}_BIN"])
                command.write_text(f"#!{sys.executable}\n{source}\n")
                command.chmod(0o755)
                result = self.run_verify()
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(sentinel, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
