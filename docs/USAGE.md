# Run Codex Action Server

Run these steps from the repository root on a Linux host with Python 3.12 or
newer, Actions Runtime 1.0.1 providing `action-server` on `PATH`, and an existing,
authenticated native Codex daemon. The host launcher starts only Action Server.
It does not start, restart, or authenticate Codex or a worker provider.

For a container deployment, use the separate
[container guide](CONTAINERS.md). The image pins Actions Runtime 1.0.2 and
has a different network and mount contract.

1. Create an operator-owned target file using
   [the example configuration](../config/targets.example.json). Keep only the
   targets you intend to expose, and replace example paths and selectors with
   your own. Preserve an existing local configuration instead of overwriting it.
2. Set the absolute configuration path and two distinct private state directories:

   ```sh
   export CODEX_ACTION_TARGETS="$PWD/config/targets.local.json"
   export CODEX_ACTION_RECEIPTS="$HOME/.local/state/codex-action-server/receipts"
   export CODEX_ACTION_DATA="$HOME/.local/state/codex-action-server/runtime"
   ```

3. To expose only reads, set `CODEX_ACTION_PROFILE=observe` in the launch
   environment. Leave it unset for the default operator catalog.
4. Start the API:

   ```sh
   bash scripts/run.sh
   ```

The launcher creates missing state directories privately and rejects existing
ones that are not private and owned by the current user. Keep both directories
persistent across restarts and upgrades.

The default address is `127.0.0.1:8088`. Set `CODEX_ACTION_PORT` to choose another
unprivileged port. Connect an MCP client to `http://127.0.0.1:8088/mcp`; the HTTP
schema is at `http://127.0.0.1:8088/openapi.json`.

### Connect to existing workers

Target configuration supports these transports:

| Transport | Connection |
| --- | --- |
| `local` | An existing local native Codex socket |
| `ssh` | An operator-configured SSH destination |
| `devsy` | An exact Devsy workspace or source match through an existing loopback SSH route |
| `devsy-kubernetes` | Direct execution in one Ready Kubernetes pod with a pinned workspace identity |
| `container` | An existing local Docker or Podman worker selected by operator configuration |

Install only the provider tools your targets need. Local targets need no provider
CLI. SSH targets need an SSH client; Devsy targets also need Devsy, and direct
Kubernetes targets need kubectl. Container targets need the selected engine CLI
and access to its local socket.

The API connects to existing workers. Provisioning and native daemon startup are
separate, explicit operations described in [remote workers](REMOTE_WORKERS.md)
and [local worker providers](LOCAL_WORKER_PROVIDER.md).

### Check the connection and read a thread

In your MCP client:

1. Call `list_targets` to see configured names.
2. Call `inspect_target` with `{"payload":{"target":"local"}}` to resolve a
   target without connecting to native Codex.
3. Call `read_server_diagnostics` for that target to check native connectivity.
4. Call `discover_threads` with the exact worktree directory:

   ```json
   {"payload":{"target":"local","cwd":"/absolute/worktree","limit":50}}
   ```

Use the returned thread ID and the same target and working directory with
`read_thread`. For discovery pagination, keep the target and directory unchanged
and pass the returned `nextCursor` as the next request's cursor.
`list_loaded_threads` lists only loaded IDs; use `discover_threads` for persisted
history. Missing or invalid working directories are rejected.

## Start work without blind retries

Persist a unique `request_id` before calling `create_thread_and_start_turn` or
`start_turn`. Reuse it only with an identical payload. The response's
`result.dispatch` records acceptance state, acknowledged native thread and turn
IDs, and retry semantics.

If the response is lost, call `read_dispatch_receipt` with the same request ID.
A repeated dispatch with that ID and payload returns its receipt without
executing again. Read the native thread or turn for current progress.

With `wait_for_completion=false`, the call returns after native acceptance.
With `true`, it observes for up to `wait_seconds`, default 15 and maximum 30.
A wait timeout preserves accepted IDs; it does not mean the turn failed.

An `unknown` receipt means acceptance could not be established. The operation may
have run. Do not submit it again with a new request ID. Legacy dispatch calls
without a request ID remain accepted, but have unsafe retry semantics.

Receipts store dispatch acknowledgements, not prompts or native results. They
are not a task database and never drive execution. Deleting a receipt removes
its replay protection. Keep receipt storage private, persistent, shared by the
API's worker processes, and on a local filesystem with supported file locking.
There is no automatic receipt expiry or retry.

Closing an API call closes its native subscription, not the running turn. Do not
assume detached client tools or approval requests will be answered. Native Codex
remains authoritative for execution and completion.

