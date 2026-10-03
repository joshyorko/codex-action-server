# Extraction design and evidence

## Contract

Trusted clients call typed actions against a stable logical name. The standalone
Action Server package owns validation, target resolution, native protocol mapping,
and dispatch receipts. Codex owns thread/turn state and authorization. Devsy owns
workspace lifecycle and SSH routes. mcp-tunnel-kit owns exposure. Friday consumes
an MCP URL. No provisioning, executor, governor, scheduler, or task database.

## Audited sources

- Friday main c58c9e150ce457034c1178777597c53f361900be; existing 20 actions,
  boundary.py, codex_rpc.py and package tests extracted, not reimplemented.
- Friday draft #44, 2ff592afd69d02c8d88fc8058d1d32cd5dfc6221: useful diagnosis and
  operator alias mapping. It maps two logical targets to one env value; this
  standalone design instead configures each logical target independently.
- Tunnel kit main d003806eda45bc7574b3c46072ef23e6706ebe80 already forwards native
  HTTP to Action Server. No agent-message adapter is needed.
- Devsy v1.19.0 a48146d3712ed32bee7672571ded299b0e490914:
  cmd/workspace/list.go emits Workspace[], cmd/workspace/status.go emits
  id/context/provider/state; pkg/ssh/config.go builds workspace ID + .devsy,
  with either loopback TCP tunnel or ProxyCommand.

## Root cause established

Read-only external local server diagnostics succeeded on Oct 2, 2026. Both old
remote target diagnostics returned CalledProcessError. Current Friday code maps
logical remote names straight to SSH destinations, then runs daemon version with
check=True. Existing host investigation receipts showed ssh -G leaving those
names literal, default user/port, and SSH failing hostname resolution. The
same investigation reached the generated Devsy alias and Codex 0.160.0 socket.
This establishes an addressing/resolution failure before native authentication,
not a claim that all future remote authentication will work.

The generated route was owned by Devsy workspace up, through a loopback listener.
The current resolver uses authoritative list/status JSON, then verifies that the
selected generated alias has the expected user and an existing TCP-loopback
configuration. It does NOT invoke workspace up/start/ssh, repair SSH config, or
select the most recently used workspace. Zero/multiple matches fail closed.
A source selector can tolerate workspace-name changes only when exactly one
workspace matches its context/provider/source. Exact workspace + optional UID is
the recommended tonight configuration. The resolver honors the selected workspace SSH config/include path, freezes the
TCP address/port/user for the native probe and proxy, and checks remote
DEVSY_WORKSPACE_UID/ID on each actual SSH connection before executing Codex.
Same-named workspaces in another context therefore fail before native RPC.
The HTTP caller supplies neither selector
nor SSH destination. ProxyCommand routes are deliberately unsupported in this
first slice because they may start lifecycle/helpers; diagnostics explain this.

## What the coordinators actually did

Read-only native thread item evidence shows Josh Room and Review using HTTP MCP
at 127.0.0.1:8087 with target local for coordinator inspection/coordination. Josh
Room's own checkpoint reports remote workspace provisioning, daemon readiness,
and a successful remote canary, while retaining host-local Action Server control
for the root and Review. Therefore their success did not prove that the external
Action Server's remote allowlist worked. The available receipts do not establish
that Review actually dispatched production work to that Devsy daemon.
Do not confuse Josh Room extension installation with its coordinator transport.

## Duplicate dispatch: proven versus unproven

Two distinct near-identical investigation threads existed and each has a final
answer (23:11 and 23:15 UTC starts). The Action Server create action performs
thread/start then turn/start on one connection, with no server retry loop. Native
request IDs restart for each connection and correlate responses; they are not
idempotency keys. Each separate tool call can create another thread. Old bounded
wait defaults to 300 seconds; an error after acceptance escapes the action and
loses its successful identity from the tool response. This is a genuine ambiguity
amplifier. Evidence does NOT prove Secure MCP Tunnel automatically retried, nor
that ChatGPT itself duplicated a single request. Caller-side transcript/transport
correlation is required to attribute those two starts definitively.

## Chosen minimal shape

Retain Actions Runtime 1.0.1 and the existing typed actions. Add operator-owned
JSON configuration, list_targets/inspect_target, and client request IDs on the
three dispatch methods. Keep synchronous observation bounded to at most30seconds.

A keyed dispatch reserves one small private JSON receipt under an OS file lock,
fsyncs it before mutation, and records native thread/turn IDs as acknowledged.
Same key+same input only reads the receipt, including after restart; different
input conflicts. Crash or missing native acknowledgement remains unknown and
never automatically re-dispatches. This is at-most-once attempt suppression,
not exactly-once native execution. No native tasks or execution state are stored.
Only hashes, IDs, acknowledgement state and fixed error class names are retained.
Unkeyed legacy requests remain compatible but explicitly report unsafe retries.

A native turn outlives the control request only according to native Codex's
behavior; closing this client never sends turn/interrupt. Do not promise detached
approval/tool callbacks: this server cannot service callbacks after disconnect.
Use native reads to establish completion, failure, interruption or waiting state.

Alternatives rejected: hard-coded current hostname (transient); an env alias for
both targets (ambiguous ownership); provisioning from a read (side effects);
transparent mutation retries (duplicate work); full task registry (Codex owns it).

## Migration boundary

Friday removes its private action package and distribution ownership. A tiny
scripts/codex_rpc.py compatibility entrypoint loads the standalone RPC client from
an installed distribution or operator-pinned CODEX_ACTION_SERVER_ROOT. The old
installer is a no-op migration notice. Friday consumes an independently managed
MCP URL via its native configuration command. Rollback uses the previously deployed
checkout, not a second maintained implementation. No live services are changed.
