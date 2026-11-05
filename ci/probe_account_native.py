#!/usr/bin/env python3
"""Positive native account/plugin reads using synthetic loopback-only fixtures.

The default path imports only the standard library and repository stdlib helpers.
--typed additionally exercises the actual typed action wrappers in a prepared CAS
development environment. No operator home, credentials, daemon, or model API is used.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlparse


METHOD_NAMES = ("account/rateLimits/read", "account/usage/read", "plugin/read")
THREAD_ID = "019fc8ab-1fb2-7000-8000-000000000123"
REMOTE_MARKETPLACE = "openai-curated-remote"
REMOTE_PLUGIN_ID = "plugins_123"


class ProbeFailure(Exception):
    """A safe fixed failure category, never an arbitrary native response body."""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--schemas", required=True, type=Path)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--typed", action="store_true")
    args = parser.parse_args()
    repo, binary = args.repo.resolve(), args.binary.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    version = subprocess.run(
        [str(binary), "--version"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
        env={
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(args.output.parent),
            "CODEX_HOME": str(args.output.parent / "version-home"),
        },
    ).stdout.strip()
    if version != f"codex-cli {args.version}":
        raise SystemExit("native binary version does not match the pinned candidate")
    sys.path.insert(0, str(repo / "ci"))
    from qualify_codex_schema import (
        _read_jsonrpc_response,
        _write_jsonrpc,
        schema_files,
    )
    from qualify_native_methods import validate

    generated = schema_files(args.schemas)
    catalog = json.loads(
        (repo / "ci/codex_qualification/native_methods.json").read_text()
    )
    catalog = {row["method"]: row for row in catalog["methods"]}
    bindings = {}
    evidence = {
        "schema_version": 1,
        "native_version": args.version,
        "binary_version": version,
        "binary_sha256": hashlib.file_digest(binary.open("rb"), "sha256").hexdigest(),
        "scope": "disposable native stdio; wholly synthetic auth; loopback mock backend; no model generation",
        "methods": {},
        "negative_cases": {},
    }
    for method in METHOD_NAMES:
        row = catalog[method]
        request, request_path = generated[row["request_schema"]]
        response, response_path = generated[row["response_schema"]]
        bindings[method] = (request, response)
        evidence["methods"][method] = {
            "status": "failed",
            "positive": False,
            "effects": {},
            "request_schema": row["request_schema"],
            "response_schema": row["response_schema"],
            "request_sha256": hashlib.file_digest(
                request_path.open("rb"), "sha256"
            ).hexdigest(),
            "response_sha256": hashlib.file_digest(
                response_path.open("rb"), "sha256"
            ).hexdigest(),
        }

    state = {"plan": "sci", "hits": [], "blocked": [], "errors": []}
    remote_plugin = {
        "id": REMOTE_PLUGIN_ID,
        "name": "fixture-plugin",
        "scope": "GLOBAL",
        "status": "AVAILABLE",
        "installation_policy": "AVAILABLE",
        "authentication_policy": "ON_USE",
        "release": {
            "display_name": "Fixture Plugin",
            "description": "Synthetic fixture only",
            "interface": {},
            "skills": [{"name": "example", "description": "Synthetic example"}],
        },
    }

    class Fixture(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, value):
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            valid = (
                self.headers.get("Authorization") == "Bearer cas-synthetic-token"
                and self.headers.get("chatgpt-account-id") == "account-123"
            )
            if not valid:
                state["errors"].append("synthetic_auth_header_mismatch")
                self.send_error(403)
            return valid

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/codex/usage":
                value = {
                    "account_id": "account-123",
                    "user_id": "user-123",
                    "plan_type": state["plan"],
                    "rate_limit": {
                        "allowed": True,
                        "limit_reached": False,
                        "primary_window": {
                            "used_percent": 42,
                            "limit_window_seconds": 3600,
                            "reset_after_seconds": 120,
                            "reset_at": 1790000000,
                        },
                    },
                }
            elif path == "/api/codex/rate-limit-reset-credits":
                value = {"credits": [], "available_count": 0, "total_earned_count": 0}
            elif path == "/api/codex/profiles/me":
                value = {
                    "stats": {
                        "lifetime_tokens": 123,
                        "peak_daily_tokens": 23,
                        "longest_running_turn_sec": 7,
                        "current_streak_days": 1,
                        "longest_streak_days": 3,
                        "daily_usage_buckets": [
                            {"start_date": "2026-11-05", "tokens": 23}
                        ],
                    }
                }
            elif path == "/ps/plugins/" + REMOTE_PLUGIN_ID:
                value = remote_plugin
            elif path == "/ps/plugins/installed":
                value = {
                    "plugins": [
                        {**remote_plugin, "enabled": True, "disabled_skill_names": []}
                    ],
                    "pagination": {"limit": 50, "next_page_token": None},
                }
            else:
                state["blocked"].append(path)
                self.send_error(403)
                return
            if self.authorized():
                state["hits"].append(path)
                self.reply(value)

        def do_POST(self):
            path = urlparse(self.path).path
            if path != "/api/codex/usage/thread_usage/query":
                state["blocked"].append("blocked_post")
                self.send_error(403)
                return
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if payload != {"thread_ids": [THREAD_ID]}:
                state["errors"].append("synthetic_thread_query_mismatch")
                self.send_error(400)
                return
            if self.authorized():
                state["hits"].append(path)
                self.reply(
                    {
                        "threads": [
                            {
                                "thread_id": THREAD_ID,
                                "estimated_usage_credits_micros": 46000000,
                                "estimated_usage_usd_micros": 1250000,
                                "native_usage_usd_micros": 750000,
                                "groups": [],
                            }
                        ]
                    }
                )

        def do_CONNECT(self):
            state["blocked"].append("blocked_external_connect")
            self.send_error(403)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f"http://127.0.0.1:{server.server_port}"

    def fail_unless(condition, category):
        if not condition:
            raise ProbeFailure(category)

    @contextmanager
    def native(authenticated):
        with tempfile.TemporaryDirectory(
            prefix="account-native-", dir=args.output.parent
        ) as tmp:
            home = Path(tmp)
            codex_home = home / "codex"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text(
                f'chatgpt_base_url = "{endpoint}"\ncli_auth_credentials_store = "file"\n'
                "[features]\nplugins = true\n"
            )
            if authenticated:

                def enc(value):
                    return (
                        base64.urlsafe_b64encode(json.dumps(value).encode())
                        .decode()
                        .rstrip("=")
                    )

                token = (
                    enc({"alg": "none", "typ": "JWT"})
                    + "."
                    + enc(
                        {
                            "https://api.openai.com/auth": {
                                "chatgpt_plan_type": "pro",
                                "chatgpt_user_id": "user-123",
                            },
                        }
                    )
                    + ".c2lnbmF0dXJl"
                )
                (codex_home / "auth.json").write_text(
                    json.dumps(
                        {
                            "auth_mode": "chatgpt",
                            "OPENAI_API_KEY": None,
                            "tokens": {
                                "id_token": token,
                                "access_token": "cas-synthetic-token",
                                "refresh_token": "cas-synthetic-refresh",
                                "account_id": "account-123",
                            },
                            "last_refresh": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                )
            env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(home),
                "CODEX_HOME": str(codex_home),
                "XDG_CACHE_HOME": str(home / "cache"),
                "XDG_CONFIG_HOME": str(home / "config"),
                "XDG_DATA_HOME": str(home / "data"),
                "HTTP_PROXY": endpoint,
                "HTTPS_PROXY": endpoint,
                "ALL_PROXY": endpoint,
                "NO_PROXY": "127.0.0.1,localhost",
            }
            process = subprocess.Popen(
                [str(binary), "app-server", "--listen", "stdio://"],
                cwd=home,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            buffer = bytearray()
            sequence = 0

            def rpc(method, params, *, error_allowed=False):
                nonlocal sequence
                if method in bindings:
                    validate(bindings[method][0], params)
                sequence += 1
                _write_jsonrpc(
                    process, {"id": sequence, "method": method, "params": params}
                )
                reply = _read_jsonrpc_response(process, buffer, sequence)
                if error_allowed:
                    return reply
                if "error" in reply:
                    raise ProbeFailure(
                        "native_rpc_error_" + str(reply["error"].get("code"))
                    )
                result = reply.get("result")
                fail_unless(isinstance(result, dict), "native_result_not_object")
                if method in bindings:
                    validate(bindings[method][1], result)
                return result

            try:
                rpc(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "cas-account-qualification",
                            "version": "1",
                        },
                        "capabilities": {"experimentalApi": True},
                    },
                )
                _write_jsonrpc(process, {"method": "initialized", "params": {}})
                yield rpc
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)

    def record(method, callback):
        row = evidence["methods"][method]
        try:
            row["effects"] = callback()
            row.update(status="passed", positive=True)
        except Exception as error:
            row.update(
                status="failed",
                positive=False,
                failure=str(error)
                if isinstance(error, ProbeFailure)
                else type(error).__name__,
            )

    def negative(name, rpc, method, params, fixed_message):
        reply = rpc(method, params, error_allowed=True)
        error = reply.get("error", {})
        passed = error.get("code") == -32600 and fixed_message in error.get(
            "message", ""
        )
        evidence["negative_cases"][name] = {
            "status": "passed" if passed else "failed",
            "method": method,
            "expected_code": -32600,
            "semantic_match": passed,
            "counts_as_positive": False,
        }

    def typed_probe(rpc, method, expected):
        if not args.typed:
            return {"status": "not_requested"}
        sys.path[:0] = [str(repo / "src"), str(repo / "tests")]
        from unittest.mock import patch
        from test_actions import load_actions

        module = load_actions()
        calls = []

        class NativeClient:
            receipts = []
            events = []

            def __init__(self, _target):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def provenance(self):
                return {"target": "synthetic-fixture", "codexBin": str(binary)}

            def request(self, name, params):
                fail_unless(name == method, "unexpected_typed_native_method")
                calls.append(params)
                return rpc(name, params)

        action, model, params, expected_params = {
            "account/rateLimits/read": (
                "read_account_rate_limits",
                "AccountRateLimitsRequest",
                {"target": "local"},
                {},
            ),
            "account/usage/read": (
                "read_account_usage",
                "AccountUsageRequest",
                {"target": "local"},
                {},
            ),
            "plugin/read": (
                "read_plugin",
                "PluginReadRequest",
                {
                    "target": "local",
                    "plugin_name": REMOTE_PLUGIN_ID,
                    "remote_marketplace_name": REMOTE_MARKETPLACE,
                },
                {
                    "pluginName": REMOTE_PLUGIN_ID,
                    "remoteMarketplaceName": REMOTE_MARKETPLACE,
                },
            ),
        }[method]
        with patch.object(module, "Client", NativeClient):
            response = getattr(module, action)(
                getattr(module, model)(**params)
            ).model_dump(mode="json")
        fail_unless(calls == [expected_params], "typed_request_mapping_mismatch")
        fail_unless(
            response["result"]["result"] == expected, "typed_result_not_preserved"
        )
        return {
            "status": "passed",
            "actual_native_dispatch": True,
            "exact_params": expected_params,
            "exact_result_preserved": True,
        }

    def deadline(_signum, _frame):
        raise TimeoutError("probe deadline exceeded")

    previous_alarm = signal.signal(signal.SIGALRM, deadline)
    signal.alarm(100)
    try:
        with native(False) as rpc:
            negative(
                "rate_limits_without_auth",
                rpc,
                "account/rateLimits/read",
                {},
                "codex account authentication required to read rate limits",
            )
            negative(
                "usage_without_auth",
                rpc,
                "account/usage/read",
                {},
                "codex account authentication required to read token usage",
            )
        with native(True) as rpc:
            negative(
                "plugin_missing_source",
                rpc,
                "plugin/read",
                {"pluginName": REMOTE_PLUGIN_ID},
                "requires exactly one of marketplacePath or remoteMarketplaceName",
            )

            def rate_limits():
                variants = {}
                for plan in ("sci", "sci_plus"):
                    state["plan"] = plan
                    result = rpc("account/rateLimits/read", {})
                    fail_unless(
                        result["rateLimits"].get("planType") == plan,
                        "plan_type_not_preserved",
                    )
                    fail_unless(
                        result["rateLimits"]["primary"]["usedPercent"] == 42,
                        "rate_window_not_observed",
                    )
                    variants[plan] = {"status": "passed", "plan_type_observed": True}
                return {
                    "variants": variants,
                    "schema_valid": True,
                    "typed_probe": typed_probe(rpc, "account/rateLimits/read", result),
                }

            record("account/rateLimits/read", rate_limits)

            def usage():
                summary = rpc("account/usage/read", {})
                fail_unless(
                    summary["summary"]["lifetimeTokens"] == 123, "summary_not_observed"
                )
                fail_unless(
                    summary["dailyUsageBuckets"][0]["tokens"] == 23,
                    "daily_bucket_not_observed",
                )
                result = rpc("account/usage/read", {"threadId": THREAD_ID})
                fail_unless(
                    result["threadUsage"]["threadId"] == THREAD_ID,
                    "thread_usage_identity_mismatch",
                )
                fail_unless(
                    result["threadUsage"]["nativeUsageUsdMicros"] == 750000,
                    "native_usage_usd_micros_not_observed",
                )
                return {
                    "unscoped_summary": True,
                    "daily_bucket": True,
                    "thread_query": True,
                    "native_usage_usd_micros": 750000,
                    "schema_valid": True,
                    "typed_probe": typed_probe(rpc, "account/usage/read", summary),
                }

            record("account/usage/read", usage)

            def plugin():
                result = rpc(
                    "plugin/read",
                    {
                        "pluginName": REMOTE_PLUGIN_ID,
                        "remoteMarketplaceName": REMOTE_MARKETPLACE,
                    },
                )
                fail_unless(
                    result["plugin"]["summary"]["name"] == "fixture-plugin",
                    "plugin_identity_mismatch",
                )
                fail_unless(
                    result["plugin"]["marketplaceName"] == REMOTE_MARKETPLACE,
                    "marketplace_identity_mismatch",
                )
                fail_unless(
                    result["plugin"]["skills"][0]["name"] == "example",
                    "remote_skill_not_observed",
                )
                return {
                    "remote_marketplace": REMOTE_MARKETPLACE,
                    "remote_skill_metadata": True,
                    "schema_valid": True,
                    "typed_probe": typed_probe(rpc, "plugin/read", result),
                }

            record("plugin/read", plugin)
    except Exception as error:
        evidence["setup_or_transport_failure"] = (
            str(error) if isinstance(error, ProbeFailure) else type(error).__name__
        )
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_alarm)
        server.shutdown()
        server.server_close()
    evidence["http_fixture"] = {
        "successful_paths": state["hits"],
        "blocked_background_requests": state["blocked"],
        "errors": state["errors"],
    }
    passed = (
        all(
            row["status"] == "passed" and row["positive"]
            for row in evidence["methods"].values()
        )
        and len(evidence["negative_cases"]) == 3
        and all(
            row["status"] == "passed" for row in evidence["negative_cases"].values()
        )
        and not state["errors"]
        and "setup_or_transport_failure" not in evidence
    )
    evidence["status"] = "passed" if passed else "failed"
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "status": evidence["status"],
                "methods": {
                    name: row["status"] for name, row in evidence["methods"].items()
                },
                "output": str(args.output),
            }
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
