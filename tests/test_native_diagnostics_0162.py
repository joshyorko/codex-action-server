"""Exact installed 0.162.0 diagnostics contract; no broader experimental grant."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import codex_rpc
from native_test_helpers import native_server_user_agent


FIXTURE = json.loads(
    (
        Path(__file__).parent
        / "fixtures/protocol/codex_0.162.0_server_diagnostics.json"
    ).read_text()
)


def client(version):
    value = codex_rpc.Client(codex_rpc.Target("local", socket_path="fixture"))
    value.ws = Mock()
    value.metadata = {"userAgent": native_server_user_agent(version)}
    return value


def test_01620_generated_diagnostics_request_and_consumed_response_shapes():
    assert FIXTURE["native_version"] == "0.162.0"
    request = FIXTURE["request_variant"]
    assert request["properties"]["method"]["enum"] == ["server/diagnostics"]
    assert request["properties"]["params"] == {
        "$ref": "#/definitions/ServerDiagnosticsParams"
    }
    params = FIXTURE["schemas"]["ServerDiagnosticsParams"]
    assert params["type"] == "object"
    assert not params.get("required") and not params.get("properties")
    response = FIXTURE["schemas"]["ServerDiagnosticsResponse"]
    assert set(response["required"]) == {"process", "gauges"}
    assert set(response["properties"]) == {"process", "gauges"}
    assert response["properties"]["process"] == {
        "$ref": "#/definitions/ServerDiagnosticsProcess"
    }
    assert response["properties"]["gauges"] == {
        "type": "array",
        "items": {"$ref": "#/definitions/ServerDiagnosticsGauge"},
    }
    process = response["definitions"]["ServerDiagnosticsProcess"]
    assert process["required"] == ["id"]
    assert process["properties"]["id"]["type"] == "integer"
    for field in ("residentMemoryBytes", "physicalFootprintBytes"):
        assert process["properties"][field]["type"] == ["integer", "null"]
    gauge = response["definitions"]["ServerDiagnosticsGauge"]
    assert set(gauge["required"]) == {"name", "value"}
    assert gauge["properties"]["name"]["type"] == "string"
    assert gauge["properties"]["value"]["type"] == "integer"


def test_01620_allows_only_reviewed_diagnostics_contract():
    native = client("0.162.0")
    result = {
        "process": {"id": 1234, "residentMemoryBytes": None},
        "gauges": [{"name": "threads", "value": 0}],
    }
    native.ws.recv.return_value = json.dumps({"id": 1, "result": result})
    assert native.request("server/diagnostics", {}) == result
    assert json.loads(native.ws.send.call_args.args[0]) == {
        "id": 1,
        "method": "server/diagnostics",
        "params": {},
    }
    assert codex_rpc.REVIEWED_EXPERIMENTAL_METHODS_BY_NATIVE_VERSION["0.162.0"] == {
        "server/diagnostics"
    }


@pytest.mark.parametrize(
    "method", sorted(codex_rpc.EXPERIMENTAL_METHODS - {"server/diagnostics"})
)
def test_01620_other_experimental_methods_remain_rejected_before_send(method):
    native = client("0.162.0")
    with pytest.raises(codex_rpc.RpcError, match="pinned experimental"):
        native.request(method, {})
    native.ws.send.assert_not_called()


@pytest.mark.parametrize(
    "version", ["0.162.1", "0.163.0", "0.162.0-beta.1", "0.162.0+build.1"]
)
def test_01620_review_does_not_authorize_unreviewed_future_builds(version):
    native = client(version)
    with pytest.raises(codex_rpc.RpcError, match="pinned experimental"):
        native.request("server/diagnostics", {})
    native.ws.send.assert_not_called()
