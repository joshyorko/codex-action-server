#!/usr/bin/env python3
"""Check the actual MCP catalog without calling native Codex or Devsy."""

import json
import os
import sys
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request


def main():
    url = (
        "http://"
        + os.environ["CODEX_ACTION_BRIDGE_GATEWAY"]
        + ":"
        + os.environ.get("CODEX_ACTION_PORT", "8088")
        + "/mcp"
    )
    opener = build_opener(ProxyHandler({}))
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    def request(payload):
        with opener.open(
            Request(url, json.dumps(payload).encode(), headers), timeout=5
        ) as response:
            session = response.headers.get("Mcp-Session-Id")
            if session:
                headers["Mcp-Session-Id"] = session
            if "id" not in payload:
                return None
            if response.headers.get_content_type() == "text/event-stream":
                data = []
                for line in response:
                    line = line.decode().rstrip("\r\n")
                    if line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif not line and data:
                        message = json.loads("\n".join(data))
                        data = []
                        if message.get("id") == payload["id"]:
                            break
                else:
                    raise ValueError("missing_rpc_response")
            else:
                message = json.load(response)
            if message.get("id") != payload["id"] or "error" in message:
                raise ValueError("failed_rpc_response")
            return message["result"]

    try:
        result = request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "cas-container-health", "version": "1"},
                },
            }
        )
        headers["MCP-Protocol-Version"] = result["protocolVersion"]
        request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        result = request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {tool["name"] for tool in result["tools"]}
        required = {
            "list_targets",
            "inspect_target",
            "read_dispatch_receipt",
            "create_thread_and_start_turn",
        }
        if len(names) != 23 or not required <= names:
            raise ValueError("unexpected_catalog")
    finally:
        if "Mcp-Session-Id" in headers:
            try:
                with opener.open(
                    Request(url, headers=headers, method="DELETE"), timeout=5
                ):
                    pass
            except HTTPError as error:
                if error.code != 405:
                    raise
    print("MCP catalog ready: 23 tools")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError):
        print("MCP catalog unavailable", file=sys.stderr)
        sys.exit(1)
