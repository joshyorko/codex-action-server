"""Package boundary tests; native transport has its own regression suite."""

from pathlib import Path
import sys
import unittest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))


class BoundaryTests(unittest.TestCase):
    def test_target_is_operator_allowlisted_before_connect(self):
        import boundary

        self.assertEqual(boundary.resolve_target("local").target, "local")
        with self.assertRaisesRegex(ValueError, "configured"):
            boundary.resolve_target("unconfigured")

    def test_targets_are_runtime_allowlisted_not_frozen_in_schema(self):
        import boundary
        from test_actions import load_actions

        with self.assertRaisesRegex(ValueError, "configured"):
            boundary.resolve_target("ror-codex.devsy")
        actions = load_actions()
        assert actions.ThreadListRequest(target="devsy").target == "devsy"

    def test_target_options_and_untrusted_cwds_are_rejected(self):
        import boundary

        with self.assertRaises(ValueError):
            boundary.resolve_target("-oProxyCommand=evil")
        with self.assertRaises(ValueError):
            boundary.validate_cwd("relative/worktree")
        with self.assertRaises(ValueError):
            boundary.validate_cwd("/trusted/../escape")
        self.assertEqual(
            boundary.validate_cwd("/trusted/worktree"), "/trusted/worktree"
        )

    def test_opaque_thread_and_turn_ids_are_nonempty_and_single_line(self):
        import boundary

        self.assertEqual(
            boundary.validate_identifier("thread-1", "thread_id"), "thread-1"
        )
        for value in ("", "  ", "thread\nforged"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    boundary.validate_identifier(value, "thread_id")


if __name__ == "__main__":
    unittest.main()
