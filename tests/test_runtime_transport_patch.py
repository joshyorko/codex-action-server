"""The image patch preserves the real SDK Host and Origin protections."""

import asyncio
import json
from pathlib import Path
import runpy

import pytest


PATCH = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts/install_runtime_transport_patch.py")
)
namespace = {}
exec(PATCH["TRANSPORT_POLICY"], namespace)
policy = namespace["_cas_transport_security"]


@pytest.mark.parametrize("runtime,sdk", [("1.0.3", "2.0.0"), ("1.0.2", "2.0.1")])
def test_patch_rejects_unreviewed_runtime_or_sdk(runtime, sdk):
    with pytest.raises(RuntimeError, match="unsupported_runtime_or_sdk_version"):
        PATCH["patch_source"]("untrusted source", runtime, sdk)


def test_patch_rejects_changed_upstream_source():
    with pytest.raises(RuntimeError, match="unexpected_runtime_adapter_source"):
        PATCH["patch_source"]("untrusted source", "1.0.2", "2.0.0")


@pytest.mark.parametrize("address", ["127.0.0.1", "172.30.86.1"])
def test_sdk_accepts_exact_listener_and_rejects_foreign_host_and_origin(address):
    import httpx
    from mcp.server import Server

    protection = policy(address, 8088)
    assert protection.enable_dns_rebinding_protection
    assert protection.allowed_hosts == [address + ":8088"]
    assert protection.allowed_origins == ["http://" + address + ":8088"]
    app = Server("transport-policy-fixture").streamable_http_app(
        stateless_http=True, json_response=True, transport_security=protection
    )
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "transport-fixture", "version": "1"},
        },
    }

    async def exercise():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://" + address + ":8088",
            ) as client:
                headers = {"Accept": "application/json, text/event-stream"}
                response = await client.post("/mcp", json=payload, headers=headers)
                assert response.status_code == 200
                assert "result" in json.loads(response.text)
                response = await client.post(
                    "/mcp",
                    json=payload,
                    headers={**headers, "Host": "foreign.invalid:8088"},
                )
                assert response.status_code == 421
                response = await client.post(
                    "/mcp",
                    json=payload,
                    headers={**headers, "Origin": "http://foreign.invalid:8088"},
                )
                assert response.status_code == 403

    asyncio.run(exercise())


@pytest.mark.parametrize("address", ["0.0.0.0", "8.8.8.8", "::1", "localhost"])
def test_transport_policy_rejects_unapproved_listeners(address):
    with pytest.raises(ValueError):
        policy(address, 8088)


def test_transport_policy_rejects_other_ports():
    with pytest.raises(ValueError):
        policy("172.30.86.1", 8089)
