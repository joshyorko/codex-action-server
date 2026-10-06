"""Typed entrypoint contracts for the bounded native control surface."""

from __future__ import annotations

import json

import pytest
from unittest.mock import Mock, patch

from test_actions import FakeClient, load_actions
import codex_rpc
from test_rpc_lifecycle import NativeProtocolFixture


class ControlClient(FakeClient):
    def request(self, method, params):
        self.calls.append((method, params))
        thread_id = params.get("threadId")
        if method == "thread/read":
            return {
                "thread": {
                    "id": thread_id,
                    "cwd": "/trusted",
                    "model": "gpt-6-luna",
                    "modelProvider": "headroom",
                    "reasoningEffort": "high",
                }
            }
        if method == "thread/start":
            return {
                "thread": {"id": "thread-new", "cwd": params["cwd"]},
                "model": params.get("model", "gpt-6-luna"),
                "modelProvider": params.get("modelProvider", "headroom"),
                "reasoningEffort": "high",
            }
        if method == "thread/resume":
            return {
                "thread": {"id": thread_id, "cwd": "/trusted"},
                "model": params.get("model", "gpt-6-luna"),
                "modelProvider": params.get("modelProvider", "headroom"),
                "reasoningEffort": params.get("effort", "high"),
            }
        if method == "thread/goal/set":
            return {"goal": {"threadId": thread_id}}
        if method in {
            "thread/settings/update",
            "turn/settings/update",
            "thread/goal/clear",
        }:
            return {"status": "applied"} if method == "turn/settings/update" else {}
        if method == "thread/goal/get":
            return {"goal": None}
        if method == "thread/turns/list":
            return {"data": [], "nextCursor": None, "backwardsCursor": None}
        if method == "thread/items/list":
            return {"data": [], "nextCursor": None, "backwardsCursor": None}
        if method == "thread/loaded/list":
            return {"data": ["thread-1"], "nextCursor": None}
        if method == "model/list":
            return {"data": [], "nextCursor": None}
        if method == "modelProvider/capabilities/read":
            return {"imageGeneration": False, "namespaceTools": True, "webSearch": True}
        if method == "server/diagnostics":
            return {"gauges": [], "process": {}}
        if method == "mcpServerStatus/list":
            return {"data": [], "nextCursor": None}
        return super().request(method, params)

    def ensure_thread_attached(self, thread_id, params, timeout=None):
        self.calls.append(("ensure_thread_attached", {"threadId": thread_id, **params}))
        return {"thread": {"id": thread_id, "cwd": "/trusted"}}


def call(module, client, function, payload):
    with patch.object(module, "Client", return_value=client):
        return function(payload)


@pytest.mark.parametrize("operation", ["start", "resume"])
@pytest.mark.parametrize(
    "observed",
    [
        {},
        {"reasoningEffort": "high"},
        {
            "model": "observed-model",
            "modelProvider": "observed-provider",
            "reasoningEffort": "high",
        },
    ],
)
def test_effort_update_preserves_observed_model_without_stale_effort(
    operation, observed
):
    module = load_actions()
    client = codex_rpc.Client(codex_rpc.Target("local", socket_path="fixture"))
    client.ws = Mock()
    client.metadata = {"userAgent": "codex-cli 0.160.1"}
    calls, pending = [], []

    def send(raw):
        message = json.loads(raw)
        calls.append(message)
        method = message["method"]
        thread = {"id": "thread-1", "cwd": "/trusted"}
        if method in ("thread/start", "thread/resume"):
            result = {
                "thread": thread,
                "model": "native-model",
                "modelProvider": "native-provider",
                "reasoningEffort": "low",
            }
        elif method == "thread/read":
            result = {"thread": {**thread, **observed}}
        elif method == "thread/settings/update":
            result = {}
        else:
            raise AssertionError(method)
        pending.append(json.dumps({"id": message["id"], "result": result}))

    client.ws.send.side_effect = send
    client.ws.recv.side_effect = lambda *args, **kwargs: pending.pop(0)
    payload = {
        "target": "local",
        "cwd": "/trusted",
        "effort": "max",
        "model": "requested-model",
        "model_provider": "requested-provider",
    }
    if operation == "start":
        function, request_type = module.start_thread, module.ThreadStartRequest
    else:
        function, request_type = module.resume_thread, module.ThreadResumeRequest
        payload["thread_id"] = "thread-1"
    with patch.object(codex_rpc.Client, "__enter__", return_value=client):
        response = call(module, client, function, request_type(**payload))
    effective = response.result.effective_configuration
    assert effective is not None
    assert effective.model == observed.get("model", "native-model")
    assert effective.model_provider == observed.get("modelProvider", "native-provider")
    assert effective.effort == observed.get("reasoningEffort")
    assert [message["method"] for message in calls] == (
        (["thread/read"] if operation == "resume" else [])
        + ["thread/" + operation, "thread/settings/update", "thread/read"]
    )
    assert response.result.result["native"]["reasoningEffort"] == "low"


def test_thread_start_maps_model_provider_and_effort_without_policy_fields():
    module = load_actions()
    client = ControlClient(None)

    response = call(
        module,
        client,
        module.start_thread,
        module.ThreadStartRequest(
            target="local",
            cwd="/trusted",
            model="gpt-6-luna",
            model_provider="headroom",
            effort="max",
        ),
    )

    assert client.calls[:2] == [
        (
            "thread/start",
            {
                "cwd": "/trusted",
                "model": "gpt-6-luna",
                "modelProvider": "headroom",
            },
        ),
        ("thread/settings/update", {"threadId": "thread-new", "effort": "max"}),
    ]
    assert response.result.effective_configuration.model == "gpt-6-luna"
    assert response.result.effective_configuration.model_provider == "headroom"


def test_new_thread_turn_materializes_fresh_thread_on_one_native_connection():
    module = load_actions()
    client = ControlClient(None)

    response = call(
        module,
        client,
        module.create_thread_and_start_turn,
        module.CreateThreadAndStartTurnRequest(
            target="local",
            cwd="/trusted",
            text="begin the bounded task",
            model="gpt-6-luna",
            model_provider="headroom",
            effort="max",
            wait_for_completion=True,
        ),
    )

    assert client.calls[:3] == [
        (
            "thread/start",
            {
                "cwd": "/trusted",
                "model": "gpt-6-luna",
                "modelProvider": "headroom",
            },
        ),
        (
            "ensure_thread_attached",
            {"threadId": "thread-new", "excludeTurns": True, "cwd": "/trusted"},
        ),
        (
            "turn/start",
            {
                "threadId": "thread-new",
                "cwd": "/trusted",
                "input": [{"type": "text", "text": "begin the bounded task"}],
                "effort": "max",
            },
        ),
    ]
    assert response.result.result["completed"]["status"] == "completed"


def test_fixed_callback_registration_preserves_settings_and_same_connection_wait():
    module = load_actions()
    client = ControlClient(None)
    response = call(
        module,
        client,
        module.create_thread_and_start_turn,
        module.CreateThreadAndStartTurnRequest(
            target="local",
            cwd="/trusted",
            text="callback proof",
            model="requested-model",
            model_provider="requested-provider",
            effort="max",
            wait_for_completion=True,
            enable_list_threads_callback=True,
        ),
    )
    method, params = client.calls[0]
    assert method == "thread/start"
    assert set(params) == {"cwd", "model", "modelProvider", "dynamicTools"}
    assert params["model"] == "requested-model"
    assert params["modelProvider"] == "requested-provider"
    (namespace,) = params["dynamicTools"]
    assert namespace["type"] == "namespace"
    assert namespace["name"] == "codex_app"
    (tool,) = namespace["tools"]
    assert tool["name"] == "list_threads"
    assert tool["type"] == "function"
    schema = tool["inputSchema"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"limit", "cursor", "cwd"}
    assert schema["properties"]["cwd"] == {"type": "string", "enum": ["/trusted"]}
    assert schema["required"] == ["cwd"]
    assert schema["properties"]["limit"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
    }
    assert client.calls[2][1]["effort"] == "max"
    assert ("wait_turn", {"threadId": "thread-new", "turnId": "turn-1"}) in client.calls
    assert response.result.result["completed"]["status"] == "completed"


def test_callback_requires_retained_wait_before_opening_connection():
    module = load_actions()
    with patch.object(module, "Client") as client:
        with pytest.raises(ValueError, match="requires wait_for_completion"):
            module.CreateThreadAndStartTurnRequest(
                target="local",
                cwd="/trusted",
                text="proof",
                enable_list_threads_callback=True,
            )
        client.assert_not_called()


def test_callback_is_default_off_strict_and_not_a_raw_registration_surface():
    module = load_actions()
    base = dict(target="local", cwd="/trusted", text="proof")
    assert (
        module.CreateThreadAndStartTurnRequest(**base).enable_list_threads_callback
        is False
    )
    schema = module.CreateThreadAndStartTurnRequest.model_json_schema()
    assert schema["properties"]["enable_list_threads_callback"]["default"] is False
    assert schema["properties"]["enable_list_threads_callback"]["type"] == "boolean"
    assert schema["additionalProperties"] is False
    for extra in (
        {"dynamicTools": []},
        {"method": "thread/start"},
        {"approvalPolicy": "never"},
        {"sandbox": "danger-full-access"},
        {"enable_list_threads_callback": "true"},
        {"enable_list_threads_callback": 1},
    ):
        with pytest.raises(ValueError):
            module.CreateThreadAndStartTurnRequest(**base, **extra)
    for request, extra in (
        (module.ThreadStartRequest, {}),
        (module.ThreadResumeRequest, {"thread_id": "thread-1"}),
    ):
        with pytest.raises(ValueError):
            request(
                target="local",
                cwd="/trusted",
                enable_list_threads_callback=True,
                **extra,
            )


def test_public_new_thread_turn_uses_native_same_connection_without_resume():
    module = load_actions()
    fixture = NativeProtocolFixture()

    class FixtureClient(codex_rpc.Client):
        def __init__(self):
            super().__init__(codex_rpc.Target("local", socket_path="fixture"))
            self.ws = fixture
            self.metadata = {"codexHome": "/codex", "userAgent": "fixture"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    client = FixtureClient()
    with patch.object(module, "Client", return_value=client):
        response = module.create_thread_and_start_turn(
            module.CreateThreadAndStartTurnRequest(
                target="local",
                cwd="/repo",
                text="materialize this fresh thread",
                effort="max",
                wait_for_completion=True,
            )
        )

    assert response.result.result["completed"]["status"] == "completed"
    methods = [message.get("method") for message in fixture.calls]
    assert methods == [
        "thread/start",
        "turn/start",
        "thread/read",
    ]
    assert "thread/resume" not in methods


def test_resume_maps_native_model_provider_and_omits_unset_overrides():
    module = load_actions()
    client = ControlClient(None)

    call(
        module,
        client,
        module.resume_thread,
        module.ThreadResumeRequest(
            target="local",
            cwd="/trusted",
            thread_id="thread-1",
            model="gpt-6-luna",
            model_provider="headroom",
        ),
    )

    assert client.calls[-1] == (
        "thread/resume",
        {
            "threadId": "thread-1",
            "cwd": "/trusted",
            "excludeTurns": True,
            "model": "gpt-6-luna",
            "modelProvider": "headroom",
        },
    )


def test_paginated_thread_turns_and_items_keep_exact_thread_cwd_guard():
    module = load_actions()
    client = ControlClient(None)

    call(
        module,
        client,
        module.list_thread_turns,
        module.ThreadTurnsListRequest(
            target="local",
            cwd="/trusted",
            thread_id="thread-1",
            limit=25,
            cursor="next-turn",
            sort_direction="asc",
            items_view="summary",
        ),
    )
    call(
        module,
        client,
        module.list_thread_items,
        module.ThreadItemsListRequest(
            target="local",
            cwd="/trusted",
            thread_id="thread-1",
            turn_id="turn-1",
            limit=25,
            cursor="next-item",
            sort_direction="desc",
        ),
    )

    assert client.calls[0] == (
        "thread/read",
        {"threadId": "thread-1", "includeTurns": False},
    )
    assert client.calls[1] == (
        "thread/turns/list",
        {
            "threadId": "thread-1",
            "limit": 25,
            "cursor": "next-turn",
            "sortDirection": "asc",
            "itemsView": "summary",
        },
    )
    assert client.calls[2] == (
        "thread/read",
        {"threadId": "thread-1", "includeTurns": False},
    )
    assert client.calls[3] == (
        "thread/items/list",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "limit": 25,
            "cursor": "next-item",
            "sortDirection": "desc",
        },
    )


def test_settings_and_goal_actions_use_native_typed_boundaries():
    module = load_actions()
    client = ControlClient(None)

    call(
        module,
        client,
        module.update_thread_settings,
        module.ThreadSettingsUpdateRequest(
            target="local", cwd="/trusted", thread_id="thread-1", effort="max"
        ),
    )
    call(
        module,
        client,
        module.update_turn_settings,
        module.TurnSettingsUpdateRequest(
            target="local",
            cwd="/trusted",
            thread_id="thread-1",
            turn_id="turn-1",
            model="gpt-6-luna",
            effort="max",
        ),
    )
    call(
        module,
        client,
        module.set_thread_goal,
        module.ThreadGoalSetRequest(
            target="local",
            cwd="/trusted",
            thread_id="thread-1",
            objective="finish the bounded task",
            status="active",
            token_budget=1000,
        ),
    )
    call(
        module,
        client,
        module.clear_thread_goal,
        module.ThreadGoalClearRequest(
            target="local", cwd="/trusted", thread_id="thread-1"
        ),
    )

    assert (
        "thread/settings/update",
        {"threadId": "thread-1", "effort": "max"},
    ) in client.calls
    assert (
        "turn/settings/update",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "model": "gpt-6-luna",
            "effort": "max",
        },
    ) in client.calls
    assert (
        "thread/goal/set",
        {
            "threadId": "thread-1",
            "objective": "finish the bounded task",
            "status": "active",
            "tokenBudget": 1000,
        },
    ) in client.calls


def test_goal_set_rejects_a_native_response_without_matching_goal_identity():
    module = load_actions()
    client = ControlClient(None)
    request = client.request

    def malformed_goal(method, params):
        if method == "thread/goal/set":
            client.calls.append((method, params))
            return {}
        return request(method, params)

    client.request = malformed_goal
    try:
        call(
            module,
            client,
            module.set_thread_goal,
            module.ThreadGoalSetRequest(
                target="local",
                cwd="/trusted",
                thread_id="thread-1",
                objective="finish the bounded task",
            ),
        )
    except Exception:
        return
    raise AssertionError("goal response without thread identity was accepted")


def test_read_only_priority_actions_are_typed_and_target_scoped():
    module = load_actions()
    client = ControlClient(None)

    call(
        module,
        client,
        module.list_models,
        module.ModelListRequest(target="local", limit=10),
    )
    call(
        module,
        client,
        module.read_model_provider_capabilities,
        module.ModelProviderCapabilitiesRequest(target="local"),
    )
    call(
        module,
        client,
        module.read_server_diagnostics,
        module.ServerDiagnosticsRequest(target="local"),
    )
    call(
        module,
        client,
        module.list_mcp_server_status,
        module.McpServerStatusListRequest(target="local"),
    )
    call(
        module,
        client,
        module.list_loaded_threads,
        module.LoadedThreadListRequest(target="local"),
    )

    assert [method for method, _params in client.calls] == [
        "model/list",
        "modelProvider/capabilities/read",
        "server/diagnostics",
        "mcpServerStatus/list",
        "thread/loaded/list",
    ]


def test_new_request_models_reject_invalid_values_and_empty_updates():
    module = load_actions()

    invalid = (
        lambda: module.ThreadStartRequest(target="local", cwd="/trusted", model=" "),
        lambda: module.ThreadTurnsListRequest(
            target="local", cwd="/trusted", thread_id="thread-1", limit=101
        ),
        lambda: module.ThreadItemsListRequest(
            target="local",
            cwd="/trusted",
            thread_id="thread-1",
            sort_direction="ascending",
        ),
        lambda: module.McpServerStatusListRequest(target="local", detail="everything"),
        lambda: module.ThreadSettingsUpdateRequest(
            target="local", cwd="/trusted", thread_id="thread-1"
        ),
        lambda: module.ThreadGoalSetRequest(
            target="local", cwd="/trusted", thread_id="thread-1"
        ),
        lambda: module.CreateThreadAndStartTurnRequest(
            target="local", cwd="/trusted", text=""
        ),
    )
    for build in invalid:
        try:
            build()
        except Exception:
            continue
        raise AssertionError("invalid typed request was accepted")


def test_fresh_attachment_rejects_resume_overrides_before_native_request():
    client = codex_rpc.Client(codex_rpc.Target("local", socket_path="fixture"))
    client.ws = Mock()
    client.set_workstream("/repo", "thread-1")

    try:
        client.ensure_thread_attached(
            "thread-1",
            {
                "threadId": "thread-1",
                "excludeTurns": True,
                "model": "gpt-6-luna",
            },
        )
    except Exception:
        pass
    else:
        raise AssertionError("unsupported fresh-thread resume override was accepted")
    client.ws.send.assert_not_called()
