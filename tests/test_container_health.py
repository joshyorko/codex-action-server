"""The production health probe must accept the current action catalog."""

import json
import importlib.util
from pathlib import Path

import pytest

from action_catalog_contract import EXPECTED_ACTION_NAMES


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
        self.names = names
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
    assert f"{len(EXPECTED_ACTION_NAMES)} tools" in capsys.readouterr().out


def test_health_probe_rejects_stale_tool_catalog(monkeypatch):
    health = load_health()
    opener = FakeOpener(set(sorted(EXPECTED_ACTION_NAMES)[:23]))
    monkeypatch.setenv("CODEX_ACTION_BRIDGE_GATEWAY", "172.30.186.1")
    monkeypatch.setattr(health, "build_opener", lambda *_: opener)

    with pytest.raises(ValueError, match="unexpected_catalog"):
        health.main()

    assert opener.methods[-1] == "DELETE"
