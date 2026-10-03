"""A broken socket must not strand this client's proxy process."""

from unittest.mock import Mock
import subprocess

import pytest
from codex_rpc import Client, Target


def test_socket_close_failure_still_reaps_owned_proxy():
    client = Client(Target("local"))
    client.ws = Mock()
    client.ws.close.side_effect = OSError("socket close failed")
    client.proc = Mock()
    proc = client.proc
    with pytest.raises(OSError):
        client.close()
    proc.terminate.assert_called_once()
    proc.wait.assert_called_once_with(timeout=5)


def test_close_escalates_only_owned_proxy_and_is_idempotent():
    client = Client(Target("local"))
    client.ws = Mock()
    client.proc = Mock()
    proc = client.proc
    proc.wait.side_effect = [subprocess.TimeoutExpired("proxy", 5), 0]
    client.close()
    client.close()
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()


def test_already_exited_proxy_is_reaped():
    client = Client(Target("local"))
    client.proc = Mock()
    proc = client.proc
    proc.terminate.side_effect = ProcessLookupError()
    client.close()
    proc.wait.assert_called_once_with(timeout=5)
