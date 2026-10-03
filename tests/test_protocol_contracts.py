"""Version-matched native Codex protocol contracts for the typed action surface."""

from __future__ import annotations

import json
import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "src"
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "protocol"
    / "native_control_contract.json"
)
sys.path.insert(0, str(SRC))

THREAD_ID = "00000000-0000-7000-8000-000000000001"
TURN_ID = "00000000-0000-7000-8000-000000000002"
NEW_THREAD_ID = "00000000-0000-7000-8000-000000000003"
CWD = "/workspaces/fixture-worktree"


class RecordingClient:
    """Small deterministic transport double for wire-mapping assertions."""

    def __init__(self):
        self.calls = []
        self.events = []
        self.receipts = []
        self.workstreams = []
        self._provenance = {
            "target": "local",
            "codexBin": "codex",
            "socket": "/var/run/codex-fixture.sock",
            "codexHome": "/codex-fixture",
            "server": "codex-cli 0.153.4",
        }

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def provenance(self):
        return self._provenance

    def set_workstream(self, cwd, thread_id, turn_id=None):
        self.workstreams.append((cwd, thread_id, turn_id))

    def request(self, method, params):
        self.calls.append((method, params))
        if method == "thread/read":
            return {
                "thread": {
                    "id": params["threadId"],
                    "cwd": CWD,
                    "model": "gpt-6-luna",
                    "modelProvider": "headroom",
                    "reasoningEffort": "max",
                }
            }
        if method == "thread/start":
            return {
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
                "cwd": CWD,
                "model": params.get("model", "gpt-6-luna"),
                "modelProvider": params.get("modelProvider", "headroom"),
                "reasoningEffort": "high",
                "sandbox": "workspace-write",
                "thread": {"id": NEW_THREAD_ID, "cwd": CWD},
            }
        if method == "thread/resume":
            return {
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
                "cwd": CWD,
                "model": params.get("model", "gpt-6-luna"),
                "modelProvider": params.get("modelProvider", "headroom"),
                "reasoningEffort": "high",
                "sandbox": "workspace-write",
                "thread": {"id": THREAD_ID, "cwd": CWD},
            }
        if method == "turn/start":
            return {"turn": {"id": TURN_ID, "status": "inProgress"}}
        if method == "thread/settings/update":
            return {}
        if method == "turn/settings/update":
            return {"status": "inProgress"}
        if method == "thread/goal/get":
            return {"goal": None}
        if method == "thread/goal/set":
            return {
                "goal": {
                    "threadId": params["threadId"],
                    "objective": params.get(
                        "objective", "Complete the bounded verification"
                    ),
                    "status": params.get("status", "active"),
                    "tokenBudget": params.get("tokenBudget"),
                    "tokensUsed": 0,
                    "timeUsedSeconds": 0,
                    "createdAt": 1700000000,
                    "updatedAt": 1700000000,
                }
            }
        if method == "thread/goal/clear":
            return {"cleared": True}
        if method == "thread/turns/list":
            return {"data": [], "nextCursor": None, "backwardsCursor": None}
        if method == "thread/items/list":
            return {"data": [], "nextCursor": None, "backwardsCursor": None}
        if method == "model/list":
            return {"data": [], "nextCursor": None}
        if method == "modelProvider/capabilities/read":
            return {
                "imageGeneration": False,
                "namespaceTools": True,
                "webSearch": False,
            }
        if method == "server/diagnostics":
            return {"gauges": [], "process": {"id": 1}}
        if method == "mcpServerStatus/list":
            return {"data": [], "nextCursor": None}
        if method == "thread/loaded/list":
            return {"data": [], "nextCursor": None}
        raise AssertionError(f"unexpected native method: {method}")


def load_actions_with_framework_shim():
    """Load the entrypoint without registering actions in a live server."""
    fake_actions = types.ModuleType("actions")

    class FakeResponse:
        def __init__(self, result):
            self.result = result

    def action(**_kwargs):
        return lambda function: function

    fake_actions.ActionError = RuntimeError
    fake_actions.Response = FakeResponse
    fake_actions.action = action
    with patch.dict(sys.modules, {"actions": fake_actions}):
        sys.modules.pop("codex_actions", None)
        return importlib.import_module("codex_actions")


class NativeProtocolContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads(FIXTURE.read_text())

    def test_bounded_native_allowlist_contains_the_priority_controls(self):
        import codex_rpc

        self.assertTrue(
            set(self.contract["bounded_native_methods"]).issubset(codex_rpc.METHODS)
        )
        self.assertEqual(
            codex_rpc.METHODS, set(self.contract["bounded_native_methods"])
        )

    def test_fixture_is_versioned_and_explicitly_non_live(self):
        self.assertEqual(
            self.contract["provenance"],
            {
                "codex_version": "codex-cli 0.153.4",
                "schema": "experimental ClientRequest v2",
                "source": "installed Codex app-server generated schema; no live response",
            },
        )
        self.assertEqual(self.contract["wire_examples"]["kind"], "sanitized_shape_only")

    def test_camel_case_wire_mappings_are_explicit(self):
        mappings = self.contract["wire_mappings"]
        self.assertEqual(
            mappings["ThreadStartRequest"]["thread/start"]["model_provider"],
            "modelProvider",
        )
        self.assertEqual(
            mappings["ThreadResumeRequest"]["thread/resume"]["exclude_turns"],
            "excludeTurns",
        )
        self.assertEqual(
            mappings["TurnSteerRequest"]["turn/steer"]["turn_id"],
            "expectedTurnId",
        )
        self.assertEqual(
            mappings["ThreadGoalSetRequest"]["thread/goal/set"]["token_budget"],
            "tokenBudget",
        )
        for mapping in mappings.values():
            for native_fields in mapping.values():
                self.assertNotIn("model_provider", native_fields.values())

    def test_native_enum_literals_and_response_shapes_are_pinned(self):
        self.assertEqual(
            self.contract["enum_literals"]["sortDirection"], ["asc", "desc"]
        )
        self.assertEqual(
            self.contract["enum_literals"]["mcpServerStatus.detail"],
            ["full", "toolsAndAuthOnly"],
        )
        self.assertEqual(
            self.contract["response_shapes"]["turn/settings/update"]["required"],
            ["status"],
        )
        self.assertEqual(
            self.contract["response_shapes"]["server/diagnostics"]["required"],
            ["gauges", "process"],
        )
        for method, shape in self.contract["response_shapes"].items():
            if method not in {
                "thread/goal/get",
                "turn/interrupt",
                "thread/settings/update",
            }:
                self.assertTrue(shape["required"], method)

    def test_thread_start_maps_provider_and_effort_to_supported_boundaries(self):
        module = load_actions_with_framework_shim()
        client = RecordingClient()
        request = module.ThreadStartRequest(
            target="local",
            cwd=CWD,
            model="gpt-6-luna",
            model_provider="headroom",
            effort="max",
        )
        with patch.object(module, "Client", return_value=client):
            module.start_thread(request)
        self.assertEqual(
            client.calls[:2],
            [
                (
                    "thread/start",
                    {
                        "cwd": CWD,
                        "model": "gpt-6-luna",
                        "modelProvider": "headroom",
                    },
                ),
                (
                    "thread/settings/update",
                    {"threadId": NEW_THREAD_ID, "effort": "max"},
                ),
            ],
        )

    def test_thread_resume_maps_provider_and_preserves_same_thread_identity(self):
        module = load_actions_with_framework_shim()
        client = RecordingClient()
        request = module.ThreadResumeRequest(
            target="local",
            cwd=CWD,
            thread_id=THREAD_ID,
            model="gpt-6-luna",
            model_provider="headroom",
            effort="max",
        )
        with patch.object(module, "Client", return_value=client):
            module.resume_thread(request)
        self.assertEqual(
            client.calls[1],
            (
                "thread/resume",
                {
                    "threadId": THREAD_ID,
                    "cwd": CWD,
                    "excludeTurns": True,
                    "model": "gpt-6-luna",
                    "modelProvider": "headroom",
                },
            ),
        )
        self.assertEqual(client.calls[2][0], "thread/settings/update")
        self.assertEqual(client.calls[2][1], {"threadId": THREAD_ID, "effort": "max"})
        self.assertTrue(all("turn/start" not in method for method, _ in client.calls))

    def test_turn_settings_updates_active_turn_without_interrupt_or_replay(self):
        module = load_actions_with_framework_shim()
        client = RecordingClient()
        request = module.TurnSettingsUpdateRequest(
            target="local",
            cwd=CWD,
            thread_id=THREAD_ID,
            turn_id=TURN_ID,
            model="gpt-6-luna",
            effort="max",
        )
        with patch.object(module, "Client", return_value=client):
            module.update_turn_settings(request)
        self.assertEqual(
            client.calls[-1],
            (
                "turn/settings/update",
                {
                    "threadId": THREAD_ID,
                    "turnId": TURN_ID,
                    "model": "gpt-6-luna",
                    "effort": "max",
                },
            ),
        )
        self.assertNotIn("turn/interrupt", [method for method, _ in client.calls])
        self.assertNotIn("turn/start", [method for method, _ in client.calls])

    def test_page_and_goal_actions_map_all_wire_fields(self):
        module = load_actions_with_framework_shim()
        client = RecordingClient()
        with patch.object(module, "Client", return_value=client):
            module.list_thread_turns(
                module.ThreadTurnsListRequest(
                    target="local",
                    cwd=CWD,
                    thread_id=THREAD_ID,
                    limit=7,
                    cursor="opaque-next-cursor",
                    sort_direction="desc",
                    items_view="full",
                )
            )
            self.assertEqual(
                client.calls[-1],
                (
                    "thread/turns/list",
                    {
                        "threadId": THREAD_ID,
                        "limit": 7,
                        "cursor": "opaque-next-cursor",
                        "sortDirection": "desc",
                        "itemsView": "full",
                    },
                ),
            )
            module.list_thread_items(
                module.ThreadItemsListRequest(
                    target="local",
                    cwd=CWD,
                    thread_id=THREAD_ID,
                    turn_id=TURN_ID,
                    limit=8,
                    cursor="opaque-items-cursor",
                    sort_direction="asc",
                )
            )
            self.assertEqual(
                client.calls[-1],
                (
                    "thread/items/list",
                    {
                        "threadId": THREAD_ID,
                        "turnId": TURN_ID,
                        "limit": 8,
                        "cursor": "opaque-items-cursor",
                        "sortDirection": "asc",
                    },
                ),
            )
            module.set_thread_goal(
                module.ThreadGoalSetRequest(
                    target="local",
                    cwd=CWD,
                    thread_id=THREAD_ID,
                    objective="Complete the bounded verification",
                    status="active",
                    token_budget=1000,
                )
            )
            self.assertEqual(
                client.calls[-1],
                (
                    "thread/goal/set",
                    {
                        "threadId": THREAD_ID,
                        "objective": "Complete the bounded verification",
                        "status": "active",
                        "tokenBudget": 1000,
                    },
                ),
            )

    def test_read_only_inventory_actions_use_empty_native_params(self):
        module = load_actions_with_framework_shim()
        client = RecordingClient()
        with patch.object(module, "Client", return_value=client):
            module.read_model_provider_capabilities(
                module.ModelProviderCapabilitiesRequest(target="local")
            )
            module.read_server_diagnostics(
                module.ServerDiagnosticsRequest(target="local")
            )
        self.assertEqual(
            client.calls,
            [
                ("modelProvider/capabilities/read", {}),
                ("server/diagnostics", {}),
            ],
        )

    def test_provider_and_effort_boundaries_match_native_schema(self):
        contracts = self.contract["method_contracts"]
        self.assertIn("modelProvider", contracts["thread/start"]["bounded_fields"])
        self.assertIn("modelProvider", contracts["thread/resume"]["bounded_fields"])
        for method in (
            "turn/start",
            "turn/steer",
            "thread/settings/update",
            "turn/settings/update",
        ):
            self.assertNotIn("modelProvider", contracts[method]["bounded_fields"])
        for method in ("thread/start", "thread/resume"):
            self.assertNotIn("effort", contracts[method]["bounded_fields"])
            self.assertEqual(contracts[method]["forbidden_native_field"], "effort")
        for method in (
            "turn/start",
            "thread/settings/update",
            "turn/settings/update",
        ):
            self.assertIn("effort", contracts[method]["bounded_fields"])
        self.assertEqual(
            contracts["turn/start"]["forbidden_native_field"], "modelProvider"
        )
        self.assertIn(
            "one running turn", contracts["turn/settings/update"]["semantics"]
        )

    def test_pagination_contracts_keep_opaque_cursors_and_native_page_fields(self):
        contracts = self.contract["method_contracts"]
        for method in (
            "thread/list",
            "thread/turns/list",
            "thread/items/list",
            "model/list",
            "mcpServerStatus/list",
            "thread/loaded/list",
        ):
            fields = contracts[method]["bounded_fields"]
            self.assertEqual(fields["limit"], "integer 1..100 at the action boundary")
            self.assertEqual(fields["cursor"], "opaque string")
        for method in (
            "thread/list",
            "thread/turns/list",
            "thread/items/list",
        ):
            self.assertEqual(
                contracts[method]["response_fields"],
                ["data", "nextCursor", "backwardsCursor"],
            )

    def test_goal_contract_is_persisted_thread_scoped_and_status_is_native(self):
        goal = self.contract["method_contracts"]["thread/goal/set"]
        self.assertIn("threadId", goal["native_required"])
        self.assertEqual(
            goal["bounded_fields"]["status"],
            "active, paused, blocked, usageLimited, budgetLimited, or complete",
        )
        for method in ("thread/goal/get", "thread/goal/set", "thread/goal/clear"):
            self.assertIn(
                "persisted thread",
                self.contract["method_contracts"][method]["ephemeral"],
            )

    def test_required_typed_actions_and_request_models_are_present(self):
        module = load_actions_with_framework_shim()
        for action_name, model_name in self.contract["actions"].items():
            with self.subTest(action_name=action_name):
                self.assertTrue(callable(getattr(module, action_name)))
                self.assertTrue(hasattr(module, model_name))

        for model_name, fields in self.contract["request_models"].items():
            with self.subTest(model_name=model_name):
                properties = getattr(module, model_name).model_json_schema()[
                    "properties"
                ]
                self.assertEqual(set(properties), set(fields))

    def test_public_models_do_not_offer_unsupported_provider_or_steer_overrides(self):
        module = load_actions_with_framework_shim()
        for model_name, forbidden in self.contract["forbidden_public_fields"].items():
            with self.subTest(model_name=model_name):
                properties = getattr(module, model_name).model_json_schema()[
                    "properties"
                ]
                self.assertTrue(set(forbidden).isdisjoint(properties))

    def test_public_enum_validation_uses_native_literals(self):
        module = load_actions_with_framework_shim()
        for value in ("asc", "desc"):
            request = module.ThreadTurnsListRequest(
                target="local",
                cwd=CWD,
                thread_id=THREAD_ID,
                sort_direction=value,
            )
            self.assertEqual(request.sort_direction, value)
        with self.assertRaises(Exception):
            module.ThreadTurnsListRequest(
                target="local",
                cwd=CWD,
                thread_id=THREAD_ID,
                sort_direction="forward",
            )
        for value in ("full", "toolsAndAuthOnly"):
            request = module.McpServerStatusListRequest(target="local", detail=value)
            self.assertEqual(request.detail, value)
        with self.assertRaises(Exception):
            module.McpServerStatusListRequest(target="local", detail="compact")

    def test_omitted_optional_settings_are_not_sent_to_native(self):
        module = load_actions_with_framework_shim()
        client = RecordingClient()
        request = module.TurnSettingsUpdateRequest(
            target="local",
            cwd=CWD,
            thread_id=THREAD_ID,
            turn_id=TURN_ID,
            model="gpt-6-luna",
        )
        with patch.object(module, "Client", return_value=client):
            module.update_turn_settings(request)
        self.assertEqual(
            client.calls[-1],
            (
                "turn/settings/update",
                {
                    "threadId": THREAD_ID,
                    "turnId": TURN_ID,
                    "model": "gpt-6-luna",
                },
            ),
        )

    def test_mcp_status_maps_detail_and_pagination_camel_case(self):
        module = load_actions_with_framework_shim()
        client = RecordingClient()
        request = module.McpServerStatusListRequest(
            target="local",
            thread_id=THREAD_ID,
            limit=3,
            cursor="opaque-mcp-cursor",
            detail="toolsAndAuthOnly",
        )
        with patch.object(module, "Client", return_value=client):
            module.list_mcp_server_status(request)
        self.assertEqual(
            client.calls[-1],
            (
                "mcpServerStatus/list",
                {
                    "threadId": THREAD_ID,
                    "limit": 3,
                    "cursor": "opaque-mcp-cursor",
                    "detail": "toolsAndAuthOnly",
                },
            ),
        )


if __name__ == "__main__":
    unittest.main()
