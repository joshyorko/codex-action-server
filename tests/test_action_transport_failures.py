"""Native handshake failures stay inside the typed action/receipt boundary."""

from unittest.mock import patch

import pytest
from websockets.exceptions import InvalidHandshake, InvalidMessage

from test_actions import load_actions


@pytest.mark.parametrize("error_type", [InvalidHandshake, InvalidMessage])
def test_read_action_sanitizes_native_handshake_failure(error_type):
    module = load_actions()
    with (
        patch.object(module, "Client") as client,
        patch.object(module, "resolve_target", return_value="fixture"),
    ):
        client.return_value.__enter__.side_effect = error_type("private-fixture-secret")
        with pytest.raises(module.ActionError, match="failed closed") as error:
            module.list_mcp_server_status(
                module.McpServerStatusListRequest(target="local", limit=1)
            )
    assert error_type.__name__ in str(error.value)
    assert "private-fixture-secret" not in str(error.value)


@pytest.mark.parametrize("error_type", [InvalidHandshake, InvalidMessage])
def test_dispatch_handshake_failure_records_unknown_without_redispatch(
    error_type, monkeypatch, tmp_path
):
    module = load_actions()
    monkeypatch.setenv("CODEX_ACTION_RECEIPTS", str(tmp_path))
    request = module.ThreadStartRequest(
        target="local", cwd="/repo", request_id="handshake-failure"
    )
    with (
        patch.object(module, "Client") as client,
        patch.object(module, "resolve_target", return_value="fixture"),
    ):
        client.return_value.__enter__.side_effect = error_type("private-fixture-secret")
        first = module.start_thread(request).result.result["dispatch"]
        replay = module.start_thread(request).result.result["dispatch"]
    assert client.call_count == 1
    assert first["state"] == replay["state"] == "unknown"
    assert first["error_code"] == replay["error_code"] == error_type.__name__
    assert first["replayed"] is False
    assert replay["replayed"] is True
    assert "private-fixture-secret" not in repr(first)
    assert "private-fixture-secret" not in "".join(
        path.read_text() for path in tmp_path.glob("*.json")
    )
