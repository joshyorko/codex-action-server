# Codex Action Server

A small typed MCP/API control surface for **existing native Codex app-server
 daemons**. Trusted client → Action Server → native Codex. Friday is a consumer.

This is not a model runtime, shell endpoint, scheduler, workspace provisioner,
or agent framework. Codex remains responsible for execution and authorization.

## Run independently

Requires Linux/Python3.12, Actions Runtime1.0.1 (`action-server`), and a running
native Codex daemon. SSH is needed for SSH/Devsy targets; Devsy1.19 is needed
only for Devsy targets. Container targets use an explicitly selected Docker or
Podman CLI and local engine socket. Local-only configuration needs no provider CLI.

```sh
cp config/targets.example.json config/targets.local.json
# Edit the operator-owned workspace selector, never a client-supplied hostname.
export CODEX_ACTION_TARGETS="$PWD/config/targets.local.json"
export CODEX_ACTION_RECEIPTS="$HOME/.local/state/codex-action-server/receipts"
export CODEX_ACTION_DATA="$HOME/.local/state/codex-action-server/runtime"
./scripts/run.sh
```

Defaults to loopback port8088 to avoid the existing Friday service on8087.
MCP is `/mcp`, OpenAPI is `/openapi.json`. The launcher starts only this API.
It never starts, restarts or authenticates Codex or Devsy.

## Targets

For the production API image and Linux Compose mount/network contract, see
[container deployment](docs/CONTAINERS.md). The tunnel kit owns the composed
Executor and tunnel-client deployment.

`list_targets` lists configured logical names. `inspect_target({payload:{target:
"devsy"}})` resolves without connecting to native Codex. Then call
`read_server_diagnostics` for connectivity and native home/socket identity.

Target configuration is local operator JSON, pointed to by CODEX_ACTION_TARGETS.
Without configuration only `local` is available. `transport:local` supports an
operator-selected codex_bin/socket_path. `transport:ssh` supports an explicit
operator-selected destination. `transport:devsy` requires context and either an
exact workspace or exact HTTPS Git source selector; optional provider and
workspace_uid narrow it. Exact selectors are preferable for tonight's migration.
Multiple matches fail closed. No latest-workspace heuristic.

Devsy list/status JSON is authoritative for workspace identity/readiness. Devsy's
generated SSH config supplies its route. This release accepts existing loopback
TCP routes only and expected user `vscode` by default; missing routes and
ProxyCommand/ProxyJump fail closed. Keep the operator's Devsy desktop/workspace
connection running. Selected workspace SSH configuration is honored and its route is pinned across
probe/proxy. Each SSH command checks the remote workspace UID/ID before invoking
Codex, rejecting stale or wrong-context aliases. Remote socket identity is discovered using native daemon
version, then the native proxy carries WebSocket RPC over SSH.

## Typed surface

The original 23 actions remain compatible: discover/read/loaded threads, turn/item pages,
models, provider capabilities, MCP inventory, diagnostics; start/create+turn,
resume/start/steer/interrupt, supported model/effort settings and coordinator
goals. Model/provider omissions preserve native configuration. New actions:
`list_targets`, `inspect_target`, `read_dispatch_receipt`, and
`list_native_capabilities`.
`list_native_capabilities` reports the selected daemon version, pinned schema
inventory, and methods intentionally absent from the current operator profile;
it does not grant methods merely because the daemon supports them. See
[native capabilities](docs/NATIVE_CAPABILITIES.md).

The API accepts stable logical target strings; the runtime allowlist rejects any
unconfigured name before connection. Thread mutations require exact cwd/thread
and turn identity where relevant. Native protocol mapping remains explicit.

### Persisted thread discovery

`discover_threads` requires an exact absolute `cwd` on every call, including
cursor pages. For example:

```json
{"payload":{"target":"local","cwd":"/absolute/worktree","limit":50}}
```

Discovery returns persisted native thread records for that worktree, including
their existing metadata and `nextCursor`. Pass the same target and cwd with the
returned cursor for the next page. Call separately for another known worktree.
An omitted, null, empty, or invalid cwd fails before native access. There is no
default cross-worktree history search or new global navigation action.

This tightens the previous optional-cwd contract: clients that omitted cwd or
sent null must supply it and refresh their MCP catalog. Existing exact-cwd
discovery and `read_thread` calls keep their response format. `read_thread`
continues to require an exact cwd and thread ID and checks both in the result.
`list_loaded_threads` remains a list of currently loaded IDs; it is not a
replacement for persisted discovery. See the [read-only workflow](docs/OPERATIONS.md#4-read-only-chatgpt-mcp-workflow)
for the Review and Josh Room worktrees.

Discovery remains read-only. Requiring scope does not grant approval or change
client safety policy; a client may still decline a call.

## Dispatch and retry

Persist your own unique request_id before calling `create_thread_and_start_turn`
or `start_turn`. Reuse it only for an identical payload. The response includes
`result.dispatch`: native thread_id/turn_id when acknowledged, state, wait mode,
replayed flag and retry semantics. After response loss call read_dispatch_receipt
with the same request_id. A repeated call returns the receipt without reconnecting
or executing again. Query the native thread/turn for current execution state.

`wait_for_completion:false` returns after native acceptance. `true` observes up
to wait_seconds (default15,max30); a wait timeout preserves accepted IDs rather
than claiming the turn failed. `unknown` means acceptance could not be established:
never switch to a new key or resubmit blindly. The native operation may have run.
Legacy calls without a key are accepted but explicitly have unsafe retry semantics.
A create-only start_thread may be ephemeral until its first turn; prefer the
combined action. Separate resume calls do not preserve a connection for later calls.

Receipts are not a task database: they contain no prompts or native results and
never drive execution. Keep the private directory persistent across upgrades and
all worker processes. Deleting a receipt removes its replay protection. No automatic
expiry or retry. Network-shared filesystems are not supported for file-lock safety.

Closing an API call closes its native subscription; it does not interrupt a turn.
Do not assume detached client tools or approvals will be answered. Native Codex
remains authoritative for execution, completion and authorization.

## Security / threat model

Clients are trusted operators with this API's authority, not mutually isolated
tenants. Any authorized turn can ask native Codex to work within its own sandbox
and approval policy. This interface does not provide a per-client cwd ACL.

Untrusted callers must never reach the API directly: bind loopback and use Secure
MCP Tunnel's authorized exposure. Do not bind to0.0.0.0. There is no generic shell,
filesystem, raw RPC, approval-policy or credential action. SSH argv is constructed
without a local shell; the fixed remote executable/arguments are shlex-quoted.
Callers cannot supply SSH options, destinations, config files, sockets or paths
to host executables. Operator config, PATH, SSH config and receipt directory are
trusted and must be protected by OS ownership. Native auth errors do not trigger
provider or credential fallback. Diagnostics expose selected nonsecret identity,
not raw subprocess stderr. Native thread contents are sensitive and should only
be exposed to the intended authorized client.

## Tests

```sh
uv venv .venv
uv pip install --python .venv/bin/python -e '.[test]'
.venv/bin/pytest
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
```

The real loopback MCP test requires action-server on PATH. It uses a disposable
Unix WebSocket native fixture and random loopback port, never production threads.
Mocks/fixtures prove mapping and safety, not live Devsy/ChatGPT acceptance.
See [design and evidence](docs/DESIGN.md) and [operator sequence](docs/OPERATIONS.md).

## Integration and ownership

mcp-tunnel-kit forwards native HTTP to this `/mcp` endpoint; it does not translate
Codex actions into agent messages. Set CODEX_MCP_URL to the selected loopback port.
Friday can use that same MCP URL. This repository owns the typed implementation;
Friday removes its private copy and keeps only a standalone-client compatibility shim. No live cutover or merge
is implied by a passing test or a draft PR.

## Durable process supervision

After proving the foreground path, see [SUPERVISION.md](docs/SUPERVISION.md) for
separate user-service templates, private persistent state, read-only acceptance,
and rollback. Templates are not automatically installed or activated.


## Remote native Codex worker recipe

[Remote workers](docs/REMOTE_WORKERS.md) covers Devsy create/resume and explicit
local Podman/Docker worker lifecycle,
`.devcontainer/remote-worker/devcontainer.json`, setup/verification, Headroom/RTK,
plugins, and native daemon startup. Friday only consumes the central typed API.
The worker does not run another Codex Action Server. Source-based discovery keeps
logical targets stable across workspace recreation; ambiguous matches fail closed.
