#!/usr/bin/env python3
"""Install the bounded MCP listener fix into the exact PyPI runtime wheel."""

import ast
import hashlib
from importlib.metadata import distribution, version


RUNTIME_VERSION = "1.0.2"
SDK_VERSION = "2.0.0"
SOURCE_SHA256 = "9fa379f62dcec1e3a4e7ca5eeeedaf7a00f338ed5eaa4f66d24804132b2cd864"

ORIGINAL = """        from starlette.routing import Route

        self.streamable_http_server = (
            self.mcp_server_setup_helper.server.streamable_http_app(
                streamable_http_path="/mcp",
                json_response=True,
                stateless_http=True,
            )
        )
"""

REPLACEMENT = """        from starlette.routing import Route

        from actions.server._settings import get_settings

        settings = get_settings()
        self.streamable_http_server = (
            self.mcp_server_setup_helper.server.streamable_http_app(
                streamable_http_path="/mcp",
                json_response=True,
                stateless_http=True,
                transport_security=_cas_transport_security(settings.address, settings.port),
            )
        )
"""

# This function is appended to the owning runtime adapter, not an alternate server.
TRANSPORT_POLICY = """

def _cas_transport_security(address, port):
    from ipaddress import IPv4Address, IPv4Network
    from mcp.server.transport_security import TransportSecuritySettings

    listener = IPv4Address(address)
    networks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    if port != 8088 or (
        listener != IPv4Address("127.0.0.1")
        and not any(listener in IPv4Network(network) for network in networks)
    ):
        raise ValueError("unsupported_codex_control_listener")
    authority = str(listener) + ":8088"
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[authority],
        allowed_origins=["http://" + authority],
    )
"""


def patch_source(source, runtime_version, sdk_version):
    if runtime_version != RUNTIME_VERSION or sdk_version != SDK_VERSION:
        raise RuntimeError("unsupported_runtime_or_sdk_version")
    if hashlib.sha256(source.encode()).hexdigest() != SOURCE_SHA256:
        raise RuntimeError("unexpected_runtime_adapter_source")
    if source.count(ORIGINAL) != 1:
        raise RuntimeError("unexpected_runtime_adapter_shape")
    patched = source.replace(ORIGINAL, REPLACEMENT) + TRANSPORT_POLICY
    ast.parse(patched)
    return patched


def main():
    runtime = distribution("actions-runtime")
    path = runtime.locate_file("actions/server/_api_action_routes.py")
    patched = patch_source(path.read_text(), runtime.version, version("mcp"))
    path.write_text(patched)
    print("Installed exact-listener MCP protection for actions-runtime 1.0.2")


if __name__ == "__main__":
    main()
