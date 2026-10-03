"""Run with the package environment to catch CLI/library namespace drift."""

import os
from pathlib import Path
import subprocess
import sys
import unittest


class RuntimeImportTests(unittest.TestCase):
    def test_cli_can_import_version_and_collect_entrypoints(self):
        package = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import actions; assert actions.__version__ == '1.0.1'; "
                "import codex_actions; "
                "assert callable(codex_actions.discover_threads)",
            ],
            cwd=package,
            env={**os.environ, "PYTHONPATH": str(package / "src")},
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
