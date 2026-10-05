from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "scripts/remote/setup.sh"


class RemoteSetupRetainedConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("brew", "gh", "python", "headroom", "rtk", "codex"):
            path = self.bin / name
            path.write_text(
                "#!/bin/sh\n"
                'printf "%s %s CODEX_HOME=%s\\n" "$(basename "$0")" "$*" "$CODEX_HOME" >> "$CODEX_WORKER_TEST_CALLS"\n'
            )
            path.chmod(0o755)
        self.codex_home = self.root / ".codex"
        self.codex_home.mkdir()
        self.config = self.codex_home / "config.toml"
        self.config.write_text(
            'model = "operator-model"\n'
            'model_provider = "operator-provider"\n'
            'model_reasoning_effort = "low"\n'
            "\n[model_providers.operator-provider]\n"
            'base_url = "http://provider.example/v1"\n'
        )
        self.calls = self.root / "calls"
        self.env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.root),
            "CODEX_WORKER_BREW": str(self.bin / "brew"),
            "CODEX_WORKER_CODEX_BIN": str(self.bin / "codex"),
            "CODEX_WORKER_GH_BIN": str(self.bin / "gh"),
            "CODEX_WORKER_PYTHON": sys.executable,
            "CODEX_WORKER_HEADROOM_BIN": str(self.bin / "headroom"),
            "CODEX_WORKER_RTK_BIN": str(self.bin / "rtk"),
            "CODEX_WORKER_CODEX_HOME": str(self.codex_home),
            "CODEX_WORKER_CODEX_CONFIG": str(self.config),
            "CODEX_WORKER_TEST_CALLS": str(self.calls),
        }

    def run_setup(self):
        return subprocess.run(
            ["bash", str(SETUP)], env=self.env, text=True, capture_output=True
        )

    def test_setup_preserves_explicit_model_provider_and_skips_headroom_rewrite(self):
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        parsed = tomllib.loads(self.config.read_text())
        self.assertEqual(parsed["model"], "operator-model")
        self.assertEqual(parsed["model_provider"], "operator-provider")
        self.assertEqual(
            parsed["model_providers"]["operator-provider"]["base_url"],
            "http://provider.example/v1",
        )
        self.assertEqual(parsed["sandbox_mode"], "workspace-write")
        self.assertNotIn("headroom init", self.calls.read_text())

    def test_setup_preserves_retained_native_default_provider(self):
        self.config.write_text(
            'model = "operator-model"\nmodel_reasoning_effort = "low"\n'
        )
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        parsed = tomllib.loads(self.config.read_text())
        self.assertEqual(parsed["model"], "operator-model")
        self.assertEqual(parsed["model_reasoning_effort"], "low")
        self.assertEqual(parsed["sandbox_mode"], "workspace-write")
        self.assertNotIn("headroom init", self.calls.read_text())

    def test_setup_rejects_config_outside_selected_codex_home_before_side_effects(self):
        self.env["CODEX_WORKER_CODEX_CONFIG"] = str(self.root / "elsewhere.toml")
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CODEX_WORKER_CODEX_CONFIG", (result.stdout + result.stderr))
        self.assertFalse(self.calls.exists())

    def test_setup_uses_inherited_codex_home_for_config_and_tools(self):
        self.env.pop("CODEX_WORKER_CODEX_HOME")
        self.env.pop("CODEX_WORKER_CODEX_CONFIG")
        self.env["CODEX_HOME"] = str(self.codex_home)
        self.config.write_text('model_provider = "operator-provider"\n')
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls.read_text()
        self.assertIn(f"CODEX_HOME={self.codex_home}", calls)

    def test_setup_uses_explicit_friday_home_to_derive_config_path(self):
        self.env.pop("CODEX_WORKER_CODEX_CONFIG")
        self.config.write_text('model_provider = "operator-provider"\n')
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        parsed = tomllib.loads(self.config.read_text())
        self.assertEqual(parsed["model_provider"], "operator-provider")
        self.assertEqual(parsed["sandbox_mode"], "workspace-write")

    def test_setup_installs_missing_codex_binary_at_selected_destination(self):
        destination = self.root / "selected-bin" / "codex"
        destination.parent.mkdir()
        (self.bin / "codex").unlink()
        self.env["CODEX_WORKER_CODEX_BIN"] = str(destination)
        self.env["CODEX_WORKER_TEST_EXPECTED_INSTALL_DIR"] = str(destination.parent)
        self.env["CODEX_WORKER_CURL_BIN"] = str(self.bin / "curl")
        curl = self.bin / "curl"
        curl.write_text(
            "#!/bin/sh\n"
            "while [ $# -gt 0 ]; do\n"
            '  if [ "$1" = "-o" ]; then output="$2"; shift 2; else shift; fi\n'
            "done\n"
            "cat >\"$output\" <<'INSTALLER'\n"
            "#!/bin/sh\n"
            '[ "$CODEX_INSTALL_DIR" = "$CODEX_WORKER_TEST_EXPECTED_INSTALL_DIR" ] || exit 12\n'
            'mkdir -p "$CODEX_INSTALL_DIR"\n'
            "printf '#!/bin/sh\\n' > \"$CODEX_INSTALL_DIR/codex\"\n"
            'chmod +x "$CODEX_INSTALL_DIR/codex"\n'
            "INSTALLER\n"
        )
        curl.chmod(0o755)
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(destination.is_file())

    def test_fresh_setup_writes_defaults_and_installs_headroom_routing(self):
        self.config.unlink()
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        created = self.config.read_text()
        self.assertIn('model = "gpt-6-luna"', created)
        self.assertIn("requires_openai_auth = true", created)
        parsed = tomllib.loads(created)
        self.assertEqual(parsed["sandbox_mode"], "workspace-write")
        self.assertEqual(parsed["approval_policy"], "on-request")
        self.assertEqual(parsed["approvals_reviewer"], "user")
        self.assertTrue(parsed["sandbox_workspace_write"]["network_access"])
        self.assertEqual(
            parsed["sandbox_workspace_write"]["writable_roots"], ["/workspaces"]
        )
        self.assertIn("headroom init --global --proxy-url", self.calls.read_text())

    def test_setup_preserves_explicit_worker_policy_and_fills_only_missing_keys(self):
        self.config.write_text(
            'model = "operator-model"\n'
            'approval_policy = "never"\n'
            'approvals_reviewer = "auto_review"\n'
            "sandbox_workspace_write = { network_access = false }\n"
            'sandbox_mode = "read-only"\n'
        )
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        parsed = tomllib.loads(self.config.read_text())
        self.assertEqual(parsed["sandbox_mode"], "read-only")
        self.assertEqual(parsed["approval_policy"], "never")
        self.assertEqual(parsed["approvals_reviewer"], "auto_review")
        self.assertFalse(parsed["sandbox_workspace_write"]["network_access"])
        self.assertEqual(
            parsed["sandbox_workspace_write"]["writable_roots"], ["/workspaces"]
        )

    def test_setup_preserves_explicit_workspace_write_roots(self):
        self.config.write_text(
            'sandbox_workspace_write = { network_access = false, writable_roots = ["/operator"] }\n'
        )
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        parsed = tomllib.loads(self.config.read_text())
        self.assertFalse(parsed["sandbox_workspace_write"]["network_access"])
        self.assertEqual(
            parsed["sandbox_workspace_write"]["writable_roots"], ["/operator"]
        )

    def test_setup_is_idempotent_for_retained_config(self):
        first = self.run_setup()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        configured = self.config.read_text()
        second = self.run_setup()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(self.config.read_text(), configured)

    def test_fresh_setup_uses_operator_proxy_endpoint(self):
        self.config.unlink()
        self.env["CODEX_WORKER_HEADROOM_URL"] = "http://proxy.internal:8080/v1"
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            'base_url = "http://proxy.internal:8080/v1"', self.config.read_text()
        )
        self.assertIn(
            "--proxy-url http://proxy.internal:8080/v1", self.calls.read_text()
        )


if __name__ == "__main__":
    unittest.main()
