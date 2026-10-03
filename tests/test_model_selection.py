"""Narrow model/effort overrides through the existing typed turn action."""

import unittest
from unittest.mock import patch
from test_actions import FakeClient, load_actions


class ModelSelectionTests(unittest.TestCase):
    def test_explicit_model_effort_reach_native_turn_without_policy_changes(self):
        module = load_actions()
        client = FakeClient(None)
        request = module.TurnStartRequest(
            target="local",
            cwd="/trusted",
            thread_id="thread-1",
            text="continue",
            model="gpt-6-luna",
            effort="max",
        )
        with patch.object(module, "Client", return_value=client):
            module.start_turn(request)
        self.assertEqual(
            client.calls[-1],
            (
                "turn/start",
                {
                    "threadId": "thread-1",
                    "cwd": "/trusted",
                    "input": [{"type": "text", "text": "continue"}],
                    "model": "gpt-6-luna",
                    "effort": "max",
                },
            ),
        )
        self.assertEqual(
            client.calls[-2],
            (
                "thread/resume",
                {
                    "threadId": "thread-1",
                    "excludeTurns": True,
                },
            ),
        )


if __name__ == "__main__":
    unittest.main()
