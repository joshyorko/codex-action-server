#!/usr/bin/env python3
"""Credential-free, disposable native CAS stable-write qualification."""

import argparse
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
import time

from qualify_codex_schema import schema_files, _write_jsonrpc, _read_jsonrpc_response
from qualify_native_methods import validate

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument("--schemas", type=Path, required=True)
parser.add_argument("--binary", type=Path, required=True)
parser.add_argument("--version", required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--fixture-label", default="disposable-closed-loopback-provider")
args = parser.parse_args()
ROOT, SCHEMAS, BINARY, OUT = args.repo, args.schemas, args.binary, args.output
reported_version = subprocess.run(
    [str(BINARY), "--version"],
    capture_output=True,
    text=True,
    timeout=10,
    check=True,
    env={
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(OUT.parent),
        "CODEX_HOME": str(OUT.parent / "version-home"),
    },
).stdout.strip()
if reported_version != f"codex-cli {args.version}":
    raise SystemExit("native binary does not match pinned candidate version")
OUT.parent.mkdir(parents=True, exist_ok=True)

catalog = json.loads((ROOT / "ci/codex_qualification/native_methods.json").read_text())
rows = {r["method"]: r for r in catalog["methods"]}
schemas = schema_files(SCHEMAS)
METHODS = [
    "review/start",
    "thread/fork",
    "thread/goal/clear",
    "thread/goal/set",
    "thread/metadata/update",
    "thread/resume",
    "thread/revert",
    "thread/start",
    "thread/unarchive",
    "turn/start",
    "turn/steer",
    "thread/read",
    "thread/items/list",
    "thread/turns/list",
]
proofs = {
    "binary_sha256": hashlib.file_digest(BINARY.open("rb"), "sha256").hexdigest(),
    "native_version": args.version,
    "fixture": args.fixture_label,
    "methods": {},
    "cleanup": "pending",
}
for method in METHODS:
    row = rows[method]
    request_name = row.get("request_schema") or row.get("native_request_type")
    response_name = row.get("response_schema") or row.get("native_response_type")
    proofs["methods"][method] = {
        "status": "not_run",
        "request_schema": request_name,
        "response_schema": response_name,
        "request_sha256": hashlib.file_digest(
            schemas[request_name][1].open("rb"), "sha256"
        ).hexdigest(),
        "response_sha256": hashlib.file_digest(
            schemas[response_name][1].open("rb"), "sha256"
        ).hexdigest(),
    }


def check(condition, message):
    if not condition:
        raise ValueError(message)


def run():
    with tempfile.TemporaryDirectory(prefix="disposable-", dir=OUT.parent) as scratch:
        home = Path(scratch)
        codex_home = home / "codex"
        codex_home.mkdir()
        called = threading.Event()
        release = threading.Event()
        requests = []

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                data = json.loads(body)
                index = len(requests)
                requests.append({"model": data.get("model")})
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                response = {
                    "id": "qualification-" + str(index),
                    "object": "response",
                    "created_at": int(time.time()),
                    "status": "in_progress",
                    "output": [],
                }

                def send(kind, **fields):
                    event = {"type": kind, **fields}
                    self.wfile.write(
                        (
                            "event: " + kind + "\ndata: " + json.dumps(event) + "\n\n"
                        ).encode()
                    )
                    self.wfile.flush()

                try:
                    send("response.created", response=response)
                    called.set()
                    if index == 0:
                        release.wait(12)
                    item = {
                        "id": "qualification-message-" + str(index),
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Disposable qualification complete.",
                                "annotations": [],
                            }
                        ],
                    }
                    send(
                        "response.output_item.added",
                        output_index=0,
                        item={**item, "content": []},
                    )
                    send(
                        "response.output_text.delta",
                        item_id=item["id"],
                        output_index=0,
                        content_index=0,
                        delta="Disposable qualification complete.",
                    )
                    send("response.output_item.done", output_index=0, item=item)
                    send(
                        "response.completed",
                        response={**response, "status": "completed", "output": [item]},
                    )
                except (BrokenPipeError, ConnectionResetError):
                    pass

        provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        provider.daemon_threads = True
        threading.Thread(
            target=provider.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        ).start()
        (codex_home / "config.toml").write_text(
            'model = "gpt-5.5"\nmodel_provider = "cas-qualification"\n'
            '[model_providers.cas-qualification]\nname = "Local qualification"\n'
            f'base_url = "http://127.0.0.1:{provider.server_port}"\nwire_api = "responses"\n'
            "requires_openai_auth = false\nsupports_websockets = false\nrequest_max_retries = 0\nstream_max_retries = 0\n"
        )
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "CODEX_HOME": str(codex_home),
            "XDG_CACHE_HOME": str(home / "cache"),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_DATA_HOME": str(home / "data"),
        }
        process = subprocess.Popen(
            [str(BINARY), "app-server", "--listen", "stdio://"],
            cwd=home,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        buffer = bytearray()
        sequence = 0

        def rpc(method, params, *, reject=False):
            nonlocal sequence
            sequence += 1
            if method in proofs["methods"]:
                validate(
                    schemas[proofs["methods"][method]["request_schema"]][0], params
                )
            _write_jsonrpc(
                process, {"id": sequence, "method": method, "params": params}
            )
            reply = _read_jsonrpc_response(process, buffer, sequence)
            if reject:
                check("error" in reply, "expected native rejection was not returned")
                code = reply["error"].get("code")
                check(code == -32600, "unexpected negative RPC code")
                if method == "review/start":
                    check(
                        "thread not found" in str(reply["error"].get("message", "")),
                        "wrong unloaded-review rejection",
                    )
                    category = "thread_not_loaded"
                else:
                    category = "wrong_turn"
                return {"rejected": True, "error_code": code, "error_class": category}
            if "error" in reply:
                raise ValueError(
                    "native_rpc_error_"
                    + str(reply["error"].get("code"))
                    + ": "
                    + str(reply["error"].get("message", ""))[:350].replace(
                        str(home), "<disposable>"
                    )
                )
            result = reply.get("result")
            check(isinstance(result, dict), "native result not an object")
            if method in proofs["methods"]:
                validate(
                    schemas[proofs["methods"][method]["response_schema"]][0], result
                )
            return result

        def probe(method, function):
            try:
                effects = function()
                proofs["methods"][method].update(
                    status="passed", positive=True, effects=effects
                )
                print(method + ": passed", flush=True)
                return effects
            except Exception as error:
                proofs["methods"][method].update(
                    status="blocked",
                    reason=str(error).replace(str(home), "<disposable>")[:500],
                )
                print(
                    method + ": blocked: " + proofs["methods"][method]["reason"],
                    flush=True,
                )
                return None

        def read(thread_id):
            return rpc("thread/read", {"threadId": thread_id, "includeTurns": True})[
                "thread"
            ]

        def wait_done(thread_id, turn_id):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                thread = read(thread_id)
                matches = [t for t in thread.get("turns", []) if t.get("id") == turn_id]
                if matches and matches[0]["status"] in [
                    "completed",
                    "failed",
                    "interrupted",
                ]:
                    check(
                        matches[0]["status"] == "completed",
                        "synthetic turn ended " + matches[0]["status"],
                    )
                    return matches[0]
                time.sleep(0.1)
            raise ValueError("synthetic turn did not complete by deadline")

        try:
            rpc(
                "initialize",
                {
                    "clientInfo": {
                        "name": "cas-stable-write-qualification",
                        "version": "1",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
            _write_jsonrpc(process, {"method": "initialized", "params": {}})
            state = {}

            def start():
                result = rpc(
                    "thread/start",
                    {
                        "cwd": str(home),
                        "approvalPolicy": "never",
                        "sandbox": "read-only",
                    },
                )
                thread = result["thread"]
                check(
                    bool(thread["id"]) and thread["cwd"] == str(home),
                    "created thread identity mismatch",
                )
                state["thread"] = thread["id"]
                return {
                    "thread_id_present": True,
                    "cwd_matches": True,
                    "history_mode_omitted": True,
                }

            if not probe("thread/start", start):
                return
            tid = state["thread"]

            def start_turn():
                result = rpc(
                    "turn/start",
                    {
                        "threadId": tid,
                        "cwd": str(home),
                        "input": [
                            {
                                "type": "text",
                                "text": "First disposable qualification turn.",
                            }
                        ],
                    },
                )
                state["first"] = result["turn"]["id"]
                check(
                    bool(state["first"]) and called.wait(3),
                    "local provider not reached",
                )
                return {"turn_id_present": True, "loopback_provider_reached": True}

            probe("turn/start", start_turn)

            def steer():
                wrong = rpc(
                    "turn/steer",
                    {
                        "threadId": tid,
                        "expectedTurnId": "deliberately-wrong-disposable-turn",
                        "input": [{"type": "text", "text": "Must reject wrong turn."}],
                    },
                    reject=True,
                )
                result = rpc(
                    "turn/steer",
                    {
                        "threadId": tid,
                        "expectedTurnId": state["first"],
                        "input": [
                            {"type": "text", "text": "Continue this disposable turn."}
                        ],
                    },
                )
                check(result["turnId"] == state["first"], "steer returned wrong turn")
                return {"correct_turn_accepted": True, "wrong_turn": wrong}

            probe("turn/steer", steer)
            release.set()
            if "first" not in state:
                return
            wait_done(tid, state["first"])
            proofs["methods"]["turn/start"]["effects"]["completed_observed"] = True

            def goal_set():
                result = rpc(
                    "thread/goal/set",
                    {
                        "threadId": tid,
                        "objective": "Disposable qualification goal",
                        "status": "paused",
                        "tokenBudget": 1000,
                    },
                )
                goal = result["goal"]
                check(
                    goal["threadId"] == tid
                    and goal["objective"] == "Disposable qualification goal"
                    and goal["status"] == "paused"
                    and goal["tokenBudget"] == 1000,
                    "goal set values mismatch",
                )
                got = rpc("thread/goal/get", {"threadId": tid})["goal"]
                check(got == goal, "goal did not persist")
                return {
                    "origin_omitted": True,
                    "set_values_match": True,
                    "get_matches": True,
                }

            probe("thread/goal/set", goal_set)

            def goal_clear():
                result = rpc("thread/goal/clear", {"threadId": tid})
                check(
                    result["cleared"] is True, "goal clear did not clear existing goal"
                )
                check(
                    rpc("thread/goal/get", {"threadId": tid})["goal"] is None,
                    "cleared goal still exists",
                )
                return {"origin_omitted": True, "cleared": True, "absent_on_get": True}

            probe("thread/goal/clear", goal_clear)

            def metadata():
                rpc(
                    "thread/metadata/update",
                    {
                        "threadId": tid,
                        "gitInfo": {"branch": "qualification-topic", "sha": "a" * 40},
                    },
                )
                patched = read(tid)["gitInfo"]
                check(
                    patched["branch"] == "qualification-topic"
                    and patched["sha"] == "a" * 40,
                    "metadata patch not retained",
                )
                rpc(
                    "thread/metadata/update",
                    {"threadId": tid, "gitInfo": {"branch": None}},
                )
                cleared = read(tid)["gitInfo"]
                check(
                    cleared["branch"] is None and cleared["sha"] == "a" * 40,
                    "metadata clear or omitted-field preservation failed",
                )
                return {
                    "patch_observed": True,
                    "explicit_null_cleared": True,
                    "omitted_sha_preserved": True,
                }

            probe("thread/metadata/update", metadata)
            second = rpc(
                "turn/start",
                {
                    "threadId": tid,
                    "input": [{"type": "text", "text": "Second disposable turn."}],
                },
            )["turn"]["id"]
            wait_done(tid, second)

            def thread_read():
                result = rpc("thread/read", {"threadId": tid, "includeTurns": False})
                check(
                    result["thread"]["id"] == tid
                    and result["thread"]["cwd"] == str(home),
                    "read identity mismatch",
                )
                check(
                    result["thread"]["turns"] == [], "metadata-only read hydrated turns"
                )
                hydrated = read(tid)
                check(
                    [t["id"] for t in hydrated["turns"]] == [state["first"], second],
                    "read history mismatch",
                )
                return {
                    "id_and_cwd_match": True,
                    "metadata_only_excludes_turns": True,
                    "full_history_matches": True,
                }

            probe("thread/read", thread_read)

            def turns_list():
                first_page = rpc(
                    "thread/turns/list",
                    {
                        "threadId": tid,
                        "limit": 1,
                        "sortDirection": "asc",
                        "itemsView": "full",
                    },
                )
                check(
                    [t["id"] for t in first_page["data"]] == [state["first"]],
                    "first turn page mismatch",
                )
                check(
                    isinstance(first_page.get("nextCursor"), str),
                    "turn page lacks continuation",
                )
                next_page = rpc(
                    "thread/turns/list",
                    {
                        "threadId": tid,
                        "limit": 1,
                        "sortDirection": "asc",
                        "itemsView": "full",
                        "cursor": first_page["nextCursor"],
                    },
                )
                check(
                    [t["id"] for t in next_page["data"]] == [second],
                    "continued turn page mismatch",
                )
                check(first_page["data"][0]["items"], "full turn view has no items")
                return {
                    "ascending_pages_match_owned_turns": True,
                    "opaque_cursor_continuation": True,
                    "full_items_present": True,
                }

            probe("thread/turns/list", turns_list)

            def items_list():
                first_page = rpc(
                    "thread/items/list",
                    {
                        "threadId": tid,
                        "turnId": state["first"],
                        "limit": 1,
                        "sortDirection": "asc",
                    },
                )
                check(len(first_page["data"]) == 1, "first item page empty")
                entry = first_page["data"][0]
                check(
                    entry["turnId"] == state["first"], "item page contains wrong turn"
                )
                check(
                    isinstance(first_page.get("nextCursor"), str),
                    "item page lacks continuation",
                )
                next_page = rpc(
                    "thread/items/list",
                    {
                        "threadId": tid,
                        "turnId": state["first"],
                        "limit": 25,
                        "sortDirection": "asc",
                        "cursor": first_page["nextCursor"],
                    },
                )
                check(
                    next_page["data"]
                    and all(e["turnId"] == state["first"] for e in next_page["data"]),
                    "continued items mismatch",
                )
                check(
                    entry["item"]["id"]
                    not in [e["item"]["id"] for e in next_page["data"]],
                    "item continuation repeated anchor",
                )
                return {
                    "owned_turn_filter_matches": True,
                    "nonempty_pages": True,
                    "opaque_cursor_no_repeated_anchor": True,
                }

            probe("thread/items/list", items_list)

            def fork():
                result = rpc(
                    "thread/fork",
                    {
                        "threadId": tid,
                        "cwd": str(home),
                        "excludeTurns": True,
                        "lastTurnId": state["first"],
                    },
                )
                ft = result["thread"]
                check(
                    ft["id"] != tid and ft["cwd"] == str(home), "fork identity mismatch"
                )
                ids = [t["id"] for t in read(ft["id"])["turns"]]
                check(
                    state["first"] in ids and second not in ids,
                    "fork inclusive prefix mismatch",
                )
                check(
                    second in [t["id"] for t in read(tid)["turns"]],
                    "fork changed source history",
                )
                return {
                    "new_id_and_same_cwd": True,
                    "inclusive_prefix_observed": True,
                    "source_preserved": True,
                }

            probe("thread/fork", fork)

            def resume():
                rpc("thread/unsubscribe", {"threadId": tid})
                result = rpc(
                    "thread/resume",
                    {"threadId": tid, "cwd": str(home), "excludeTurns": True},
                )
                check(
                    result["thread"]["id"] == tid
                    and result["thread"]["cwd"] == str(home),
                    "resume identity mismatch",
                )
                check(
                    second in [t["id"] for t in read(tid)["turns"]],
                    "resume lost persisted history",
                )
                return {
                    "unsubscribed_before_resume": True,
                    "id_and_cwd_match": True,
                    "persisted_history_retained": True,
                }

            probe("thread/resume", resume)

            def revert():
                rpc("thread/revert", {"threadId": tid, "beforeTurnId": second})
                ids = [t["id"] for t in read(tid)["turns"]]
                check(
                    state["first"] in ids and second not in ids,
                    "revert prefix mismatch",
                )
                return {"before_turn_removed": True, "earlier_turn_preserved": True}

            probe("thread/revert", revert)

            def unarchive():
                rpc("thread/archive", {"threadId": tid})
                result = rpc("thread/unarchive", {"threadId": tid})
                check(
                    result["thread"]["id"] == tid
                    and result["thread"]["cwd"] == str(home),
                    "unarchive identity mismatch",
                )
                check(
                    state["first"] in [t["id"] for t in read(tid)["turns"]],
                    "unarchive lost history",
                )
                return {
                    "archive_then_unarchive": True,
                    "id_and_cwd_match": True,
                    "history_retained": True,
                }

            probe("thread/unarchive", unarchive)

            def review():
                target = {
                    "type": "custom",
                    "instructions": "Return a brief disposable review; do not use tools.",
                }
                unloaded = rpc(
                    "review/start", {"threadId": tid, "target": target}, reject=True
                )
                check(
                    unloaded["error_code"] == -32600
                    and unloaded["error_class"] == "thread_not_loaded",
                    "unexpected review-unloaded precondition rejection",
                )
                rpc(
                    "thread/resume",
                    {"threadId": tid, "cwd": str(home), "excludeTurns": True},
                )
                result = rpc("review/start", {"threadId": tid, "target": target})
                check(
                    result["reviewThreadId"] == tid and bool(result["turn"]["id"]),
                    "inline review identity mismatch",
                )
                wait_done(tid, result["turn"]["id"])
                return {
                    "delivery_omitted": True,
                    "resumed_after_archive": True,
                    "same_thread_inline": True,
                    "turn_id_present": True,
                    "completed_observed": True,
                    "unloaded_thread_negative": unloaded,
                }

            probe("review/start", review)
        finally:
            release.set()
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
            provider.shutdown()
            provider.server_close()
            proofs["provider_requests"] = len(requests)
            proofs["cleanup"] = "native-process-exited-provider-closed-home-removed"


def deadline(_signum, _frame):
    raise TimeoutError("overall qualification deadline exceeded")


signal.signal(signal.SIGALRM, deadline)
signal.alarm(150)
try:
    run()
except Exception as error:
    proofs["fatal"] = str(error)[:500]
finally:
    signal.alarm(0)
    OUT.write_text(json.dumps(proofs, indent=2, sort_keys=True) + "\n")
    print("saved " + str(OUT), flush=True)
sys.exit(
    1
    if proofs.get("fatal")
    or any(row["status"] != "passed" for row in proofs["methods"].values())
    else 0
)
