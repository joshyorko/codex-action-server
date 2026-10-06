# Native capability inventory

`list_native_capabilities({target})` reports the selected daemon's `userAgent`,
the versioned CAS contract, capability families, and the current exposure
profile. Its catalog is static: a newer daemon cannot expand CAS's RPC allowlist.
The inventory is based on Codex `rust-v0.160.1`
(`d27764b82f7118f674371e6d6e76271d9d606edb`); the generated-schema baseline is
104 default client requests and 167 with experimental requests (63 additional).

The repo's checked-in native wire-contract fixture still comes from Codex
`0.153.4`. The 0.160.1 catalog is protocol-source inventory, not proof of a
live 0.160.1 acceptance run. No live daemon, credentials, or deployment are
changed by this action. The default and experimental-only `ClientRequest`
method lists are captured in
`tests/fixtures/protocol/codex_0.160.1_client_requests.json` from the pinned
`codex app-server generate-json-schema` output and checked for classification
completeness.

## Exposure

The current server profile is `operator`; ADMIN is disabled. The caller cannot
select a profile. There is no CAS ADMIN action or caller-controlled elevation
flag. The operator-selected observe-only profile work from issue #3 is not in
this checkout, so this catalog deliberately does not invent a second profile
mechanism.

OBSERVE and OPERATOR_CONTROL list the native methods currently exposed by the
strict RPC allowlist. EXPERIMENTAL distinguishes methods requiring experimental
native support; only explicitly mapped methods are exposed. Queue, search/timeline,
and background-terminal methods require the exact `codex-cli 0.160.1` user agent.
Queue inputs are restricted to bounded text
items and mutations require receipt keys. Native search has no CWD parameter,
so CAS requires an exact CWD and filters every result before returning it; raw
native search receipts are not included in that response. ADMIN inventories host,
credential, package, process, and configuration mutations but does not expose
them.

The operator profile also exposes inline review; account rate-limit/usage reads;
skills, hooks, plugins, and app inventories, plus named plugin detail reads;
exact-thread MCP resource reads and
explicit consequential MCP tool calls; and bounded background-terminal
observation/termination. Thread attachments have bounded list/add/remove
actions. MCP tool and attachment payloads are limited to 16 KiB, and MCP
resource/tool results to 1 MiB. Terminal termination requires the process ID
to match a native terminal record for the exact thread and CWD.

The `DEFERRED/UNSUPPORTED` list is intentionally not callable. It includes
section movement, which still requires a reviewed typed mapping and protocol
tests. No raw-RPC fallback exists.

## Callback blocker

Native approval, elicitation, user-input, and `item/tool/call` callbacks are
connection-scoped server-to-client JSON-RPC requests. CAS currently opens a
native WebSocket for an action and closes it when that action returns. Its
existing callback path only handles a fixed, in-action callback; it cannot
persist and resume an arbitrary native callback connection after the caller's
action ends. Therefore there is no pending-request bridge, durable response
fence, restart reconciliation, or exactly-once response guarantee. The RPC
client rejects unsupported callbacks and never approves them automatically.

Implementing this safely requires a separate callback-lifecycle design and an
end-to-end fixture that proves retained/resumable connection semantics. Until
then callback methods remain unsupported; an agent-mediated workaround is not
native parity.

## Supervisory snapshot boundary

This checkout has no bounded supervisory snapshot action from issue #2. Existing
turn/item pagination remains available, but is not represented as an integrated
snapshot projection. This capability catalog adds no state database,
summarizer, or transcript-download path. Snapshot integration remains a separate
gate.

## Rollback

Rollback is to the previously deployed CAS image/catalog. This change does not
alter target configuration, native Codex, Executor, or tunnel state. Do not
claim live acceptance based on fixture tests alone.
