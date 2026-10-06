"""The production health probe must accept the current action catalog."""

import json
import importlib.util
import ast
from pathlib import Path

import pytest

from action_catalog_contract import (
    ACTION_NAMES_BY_PROFILE,
    EXPECTED_ACTION_NAMES,
    IMPLEMENTED_PROFILE,
    action_names_for_profile,
)


ROOT = Path(__file__).parents[1]


def load_health():
    path = ROOT / "scripts/container_health.py"
    spec = importlib.util.spec_from_file_location("container_health", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeHeaders(dict):
    def get_content_type(self):
        return self.get("Content-Type", "application/json")


class FakeResponse:
    def __init__(self, payload=None):
        self.headers = FakeHeaders(
            {
                "Content-Type": "application/json",
                "Mcp-Session-Id": "health-session",
            }
        )
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class FakeOpener:
    def __init__(self, names):
        self.names = list(names)
        self.methods = []

    def open(self, request, timeout):
        assert timeout == 5
        if request.get_method() == "DELETE":
            self.methods.append("DELETE")
            return FakeResponse({})
        payload = json.loads(request.data)
        method = payload["method"]
        self.methods.append(method)
        if method == "initialize":
            return FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"protocolVersion": "2025-03-26"},
                }
            )
        if method == "notifications/initialized":
            return FakeResponse({})
        if method == "tools/list":
            return FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "tools": [{"name": name} for name in sorted(self.names)]
                    },
                }
            )
        raise AssertionError(method)


def test_health_probe_accepts_exact_current_tool_catalog(monkeypatch, capsys):
    health = load_health()
    opener = FakeOpener(EXPECTED_ACTION_NAMES)
    monkeypatch.setenv("CODEX_ACTION_BRIDGE_GATEWAY", "172.30.186.1")
    monkeypatch.setattr(health, "build_opener", lambda *_: opener)

    health.main()

    assert opener.methods == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "DELETE",
    ]
    assert "operator profile" in capsys.readouterr().out


def test_health_probe_accepts_exact_observe_catalog(monkeypatch, capsys):
    health = load_health()
    observe_names = action_names_for_profile("observe")
    opener = FakeOpener(observe_names)
    monkeypatch.setenv("CODEX_ACTION_BRIDGE_GATEWAY", "172.30.186.1")
    monkeypatch.setattr(health, "IMPLEMENTED_PROFILE", "observe")
    monkeypatch.setattr(health, "EXPECTED_TOOL_NAMES", observe_names)
    monkeypatch.setattr(health, "build_opener", lambda *_: opener)

    health.main()

    assert "observe profile" in capsys.readouterr().out
    assert set(opener.names) == observe_names


def test_action_catalog_contract_only_declares_implemented_profiles():
    assert set(ACTION_NAMES_BY_PROFILE) == {"operator", "observe"}
    assert action_names_for_profile(IMPLEMENTED_PROFILE) == EXPECTED_ACTION_NAMES
    assert action_names_for_profile("observe") <= action_names_for_profile("operator")
    with pytest.raises(ValueError, match="Unsupported action exposure profile"):
        action_names_for_profile("unknown")


def test_profile_catalogs_match_explicit_action_annotations():
    tree = ast.parse((ROOT / "src/codex_actions.py").read_text())
    all_actions = set()
    observe_actions = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        action_decorators = [
            decorator
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and getattr(decorator.func, "id", None) == "action"
        ]
        if not action_decorators:
            continue
        all_actions.add(node.name)
        consequential = next(
            keyword.value
            for keyword in action_decorators[0].keywords
            if keyword.arg == "is_consequential"
        )
        if ast.literal_eval(consequential) is False:
            observe_actions.add(node.name)

    assert all_actions == set(action_names_for_profile("operator"))
    assert observe_actions == set(action_names_for_profile("observe"))


def test_health_probe_rejects_stale_tool_catalog(monkeypatch):
    health = load_health()
    opener = FakeOpener(set(sorted(EXPECTED_ACTION_NAMES)[:23]))
    monkeypatch.setenv("CODEX_ACTION_BRIDGE_GATEWAY", "172.30.186.1")
    monkeypatch.setattr(health, "build_opener", lambda *_: opener)

    with pytest.raises(ValueError, match="unexpected_catalog"):
        health.main()

    assert opener.methods[-1] == "DELETE"


def test_health_probe_rejects_unknown_name_even_when_catalog_size_matches(
    monkeypatch,
):
    health = load_health()
    names = sorted(EXPECTED_ACTION_NAMES)
    names[-1] = "unknown_action"
    opener = FakeOpener(names)
    monkeypatch.setenv("CODEX_ACTION_BRIDGE_GATEWAY", "172.30.186.1")
    monkeypatch.setattr(health, "build_opener", lambda *_: opener)

    with pytest.raises(ValueError, match="unexpected_catalog"):
        health.main()

    assert opener.methods[-1] == "DELETE"
