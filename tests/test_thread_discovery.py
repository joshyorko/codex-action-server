"""Persisted discovery requires an explicit worktree before native access."""

from unittest.mock import patch

from pydantic import ValidationError
import pytest

from test_actions import FakeClient, load_actions


@pytest.mark.parametrize("arguments", [{}, {"cwd": None}])
def test_discovery_requires_a_nonnullable_cwd(arguments):
    actions = load_actions()
    with pytest.raises(ValidationError):
        actions.ThreadListRequest(target="local", **arguments)


def test_discovery_schema_makes_the_scope_required():
    schema = load_actions().ThreadListRequest.model_json_schema()
    assert "cwd" in schema["required"]
    assert schema["properties"]["cwd"]["type"] == "string"
    assert "default" not in schema["properties"]["cwd"]


@pytest.mark.parametrize(
    "cwd", ["", " ", "relative/path", "/work/../other", "/work\nother"]
)
def test_discovery_rejects_invalid_cwd_before_connecting(cwd):
    actions = load_actions()
    with patch.object(actions, "Client") as client:
        with pytest.raises(actions.ActionError, match="cwd"):
            actions.discover_threads(actions.ThreadListRequest(target="local", cwd=cwd))
    client.assert_not_called()


def test_discovery_keeps_exact_scope_on_every_page():
    actions = load_actions()
    client = FakeClient(None)
    with patch.object(actions, "Client", return_value=client):
        actions.discover_threads(
            actions.ThreadListRequest(
                target="local", cwd="/trusted", limit=1, cursor="page-2"
            )
        )
    assert client.calls == [
        ("thread/list", {"cwd": "/trusted", "limit": 1, "cursor": "page-2"})
    ]
