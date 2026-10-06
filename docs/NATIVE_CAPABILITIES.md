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
changed by this action.

## Exposure

The current server profile is `operator`; ADMIN is disabled. The caller cannot
select a profile. There is no CAS ADMIN action or caller-controlled elevation
flag. The operator-selected observe-only profile work from issue #3 is not in
this checkout, so this catalog deliberately does not invent a second profile
mechanism.

OBSERVE and OPERATOR_CONTROL list the native methods currently exposed by the
strict RPC allowlist. EXPERIMENTAL distinguishes methods requiring experimental
native support; only explicitly mapped methods are exposed. Queue, search, and
timeline methods remain withheld. ADMIN inventories host, credential, package,
process, and configuration mutations but does not expose them. The native schema's broader method families—including plugins/apps/hooks
inventories, review, attachments, item injection, MCP resources/tools, account
usage, and background terminals—are listed as deferred until typed request
mappings, identity checks, and protocol tests are added.

The `DEFERRED/UNSUPPORTED` list is intentionally not callable. It includes
the remaining first-party control tranche not yet implemented by CAS. No
raw-RPC fallback exists.

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
