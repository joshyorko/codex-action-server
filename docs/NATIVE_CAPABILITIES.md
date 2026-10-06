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

The default server profile is `operator`; ADMIN is disabled. An operator may
select `observe` with the process environment setting
`CODEX_ACTION_PROFILE=observe`. The setting is read at process import/startup,
is never an action argument, and takes effect only after a process restart.
Unknown values fail startup. Observe registration contains only the explicit
read-action set. Excluded actions are not registered with the Actions Runtime,
so MCP and direct HTTP action routes are both absent; direct Python entrypoint
calls fail closed as well. `list_native_capabilities` reports the configured
profile and its actual exposed native methods. A profile change requires a full
Action Server/process restart and catalog refresh; it does not change an
in-flight process. This is a server-wide trusted-reader boundary, not per-client
or per-repository isolation. No caller-selectable elevation or ADMIN action
exists.

## Required tranche status: incomplete

The exposed action count is not evidence that the issue's required operator
tranche is complete. `thread/section/move` and `app/read` now have typed
mappings, exact identity checks, and pinned 0.160.1 request/response fixtures.
`app/read` is a read operation, but its pinned `AppsReadParams` and
`AppsReadResponse` schemas explicitly mark it EXPERIMENTAL. Although
`app/read` appears in the default `ClientRequest` union, CAS requires the exact
`codex-cli 0.160.1` user agent before dispatching it.

The checked-in 0.160.1 request manifest proves method inventory and
classification completeness; the separate typed RPC contract fixture now
records request and response fields for section movement, app reads, queue,
search/occurrence, timeline, and background-terminal methods. Those fixtures
and local fake-client tests do not establish native daemon acceptance. Do not
treat the action catalog or this PR as full required-tranche parity until
pinned-version acceptance is demonstrated.

OBSERVE and OPERATOR_CONTROL list the native methods currently exposed by the
strict RPC allowlist. EXPERIMENTAL distinguishes methods requiring experimental
native support; only explicitly mapped methods are exposed. Queue, search/timeline,
and background-terminal methods require the exact `codex-cli 0.160.1` user agent.
Queue inputs are restricted to bounded text
items and mutations require receipt keys. Native search has no CWD parameter,
so CAS requires an exact CWD and filters every result before returning it; raw
native search receipts are not included in that response. ADMIN inventories host,
credential, package, process, and configuration mutation families as not exposed;
that classification is not a complete inventory of native admin parity.

The operator profile also exposes inline review; account rate-limit/usage reads;
skills, hooks, plugins, and app inventories, plus named plugin detail reads;
exact-thread MCP resource reads and
explicit consequential MCP tool calls; and bounded background-terminal
observation/termination. Thread attachments have bounded list/add/remove
actions. MCP tool and attachment payloads are limited to 16 KiB, and MCP
resource/tool results to 1 MiB. Terminal termination requires the process ID
to match a native terminal record for the exact thread and CWD.

The `DEFERRED/UNSUPPORTED` list is intentionally not callable. It contains
native operations without reviewed typed request/response contracts. No raw-RPC
fallback exists.

ADMIN remains disabled and unexposed. Its classification is a safety boundary,
not completed admin parity; high-authority operations still require separately
reviewed typed contracts and immutable/operator-owned enablement before any
exposure.

## Callback blocker

Native approval, elicitation, user-input, and `item/tool/call` callbacks are
server-initiated JSON-RPC requests on the originating WebSocket. The pinned
`rust-v0.160.1` app-server protocol defines request IDs and method-specific
responses, but no callback-resume or delayed-response RPC. In this checkout,
`_run` scopes one `Client` to an action, `Client.receive` can only consume a
callback while that request is in flight, and `Client.__exit__` closes the
WebSocket. The client sends an explicit error for unsupported callback methods
and never auto-approves them. The fixed approval bridge is limited to its
same-connection in-action workflow; it is not a durable general callback
broker.

There is no connection handle or protocol request that lets a later action
deliver a response to a request on a closed WebSocket. Safely supporting this
requires a process-owned retained-connection service, minimal fenced receipts,
restart/expiry reconciliation, and an end-to-end native fixture proving
same-connection response delivery; none exists here. This warrants a focused
follow-on, “Retain and fence native app-server callback connections,” covering
connection ownership, exact thread/turn/request identity, one-shot responses,
duplicate/conflicting replies, cancellation, timeout, restart/loss
reconciliation, and native fixture proof. Until that lifecycle is designed and
proven, callbacks remain unsupported and an agent-mediated workaround is not
native parity.

## Supervisory snapshot boundary

`get_thread_snapshot` is an additive, read-only projection for one exact
target/CWD/thread. It reads the native thread status without turns, requests at
most one newest turn with `itemsView=notLoaded`, and then at most one newest
thread item. It returns native status/active flags, latest turn status and a
classified error code, latest item kind/phase and a UTF-8-safe excerpt capped at
512 bytes, observation time, a target/thread/CWD-scoped revision, and native
continuation pointers. Command output and raw error messages are never included.

The encoded snapshot is limited to 8 KiB. A repeated revision returns a smaller
unchanged response that retains current status, wait flags, and turn error.
Unknown native status/flags remain marked unknown; a failed or mismatched native
read fails closed and cannot become an idle snapshot. Snapshot envelopes omit
duplicated RPC receipts and event payloads. Existing raw page/read tools remain
available for deliberate deeper inspection. No persistent state database,
summarizer, thread creation, resume, or mutation is added.

## Rollback

Rollback is to the previously deployed CAS image/catalog. This change does not
alter target configuration, native Codex, Executor, or tunnel state. Do not
claim live acceptance based on fixture tests alone.
