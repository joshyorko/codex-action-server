# MCP inventory and action admission acceptance

## Source repair

`src/codex_shared/integrations.py::_bounded_mcp_status_result` bounds native
inventory depth, items, strings, and bytes. It redacts credential-shaped page
metadata and nested fields, preserves opaque continuation tokens exactly, and
rejects a cursor that cannot fit. The public action omits duplicated native
receipts and events.

`src/codex_shared/common.py::_run` and `_dispatch_run` contain native WebSocket
handshake failures. Dispatch receipts retain unknown acknowledgement and replay
fencing. This repairs a proven exception-boundary gap; it does not establish the
cause or recovery of the production HTTP 502 incident.

At source revision `ba73c3ca0aa0ca8eb1fe766d8eaa1b7acb7bae35`, pinned
`action-server devenv task lint test` passed 759 tests and skipped 18. The skips
require optional built images or explicit serialized package-composition
acceptance. The regression suites are `tests/test_mcp_status_bounds.py` and
`tests/test_action_transport_failures.py`. Cursor integrity, root credential
redaction, and handshake containment each failed before their repair.

## Read-only observations on 2026-11-05

The existing managed Kamal wrapper reported the CAS container running/healthy
with image ID
`sha256:7dd42830555719d88ffcf581b2cfdcff46ff8df80f66a4da32b48ba98f4a8abc`.
The Git revision was not exposed and remains unverified. This is not the PR
image and container health does not establish native action availability.

A bounded managed CAS log capture covered 17:29:03–18:40:57 UTC. At 18:30:43
and 18:30:53 UTC, Runtime delayed `read_server_diagnostics` because one action
was already running. Earlier unrelated reads waited for the same limit.
Executor native diagnostic calls failed after approximately 30 seconds on both
Dakota routes; `list_targets` had succeeded independently. There was no
end-to-end request ID connecting every 502 to a queue wait. The identity of the
action occupying the slot and the earliest underlying native failure remain
unverified. The bounded logs contain generic fail-closed `RpcError` frames;
they do not expose an underlying SSH, socket, or handshake cause.

The queue boundary is explicit in
`scripts/start_packages.py::start`: aggregate and split package startup both
use `--min-processes 1 --max-processes 1`.
`tests/test_package_startup.py::test_compatibility_keeps_root_routes_and_one_process`
and `docs/PACKAGE_COMPOSITION.md` preserve that contract.
`src/codex_rpc.py::Client` separately defaults to 60-second connection/request
waits; those waits do not bound Runtime admission before the callback starts.
Raising the worker count alone does not guarantee availability when all workers
are occupied, and lowering a per-step timeout does not establish an end-to-end
deadline. This PR preserves those contracts.

## Remaining operational gate

Use the canonical **mcp-tunnel-kit checkout on the workstation**, with its
existing managed instance and credentials, to capture a bounded window around
the failed request. These commands only read accessory logs:

```sh
cas_triage_dir="$(mktemp -d "${TMPDIR:-/tmp}/codex-cas-triage-XXXXXXXX")"
chmod 0700 "$cas_triage_dir"
bin/kamal native accessory logs cas --since 30m --lines 300 > "$cas_triage_dir/cas.log" 2>&1
bin/kamal native accessory logs executor --since 30m --lines 300 > "$cas_triage_dir/executor.log" 2>&1
chmod 0600 "$cas_triage_dir/cas.log" "$cas_triage_dir/executor.log"
```

Inspect the capture locally for the occupying action's name, admission/start/end
timestamps, and sanitized failure class. Do not share raw logs or native
inventory. Do not send more probes into a known occupied slot, retry an unknown
dispatch, or restart services merely to clear the symptom. Remove the capture
after retaining only the sanitized conclusions needed for review.

Closing issue #22 still requires underlying route attribution and repeated
default/detailed inventory, diagnostics, and unrelated-read acceptance on both
Dakota targets through the deployed path. Any launch/deadline correction needs
a disposable slow-native fixture proving admission behavior first. Publication
and a CAS-only managed deployment remain separate owner gates; no deployment,
restart, daemon/configuration change, or dispatch was performed here.
