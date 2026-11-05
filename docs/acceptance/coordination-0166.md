# CAS #28 read-only native coordination inventory

Observed 2026-11-05 18:33 UTC. No configuration, credentials, daemon lifecycle, thread, model turn, or production service was changed. The protected Actions release thread was not read or modified.

## Dakota NVIDIA

- Host identity: `dakota-nvidia`.
- Installed standalone CLI: `0.166.0`; resolved binary is `/home/kdlocpanda/.codex/packages/standalone/releases/0.166.0-x86_64-unknown-linux-musl/bin/codex`.
- Running daemon: `codex app-server daemon version` independently reports `status=running`, `cliVersion=0.166.0`, `managedCodexVersion=0.166.0`, and `appServerVersion=0.166.0`.
- CLI and managed daemon binary SHA-256 both equal `b8a11fedf24b2dfb59a70c9e52b030f9a508dcd49cd2dd0d41956f4c785156b8`.
- Root configuration explicitly enables `features.agent_message_board`; `features.multi_agent_v2` is unset. The installed CLI resolves these as board enabled and MAv2 disabled. No selected profile or profile-specific board overrides were present. The two observed daemon/app-server processes had no command-line overrides for these two flags and no `CODEX_HOME` override. This does not prove the feature configuration already loaded into every existing thread.
- Model-visible board tool execution inside a native tree was not exercised. Existing-thread feature availability remains unverified.

## Dakota CPU and live CAS transport

- Executor's current registry exposes logical targets `dakota-nvidia` and `dakota-cpu` over operator-owned SSH routes (root agent observation).
- One Executor program called NVIDIA diagnostics successfully in 11,833 ms, then CPU diagnostics failed in 30,056 ms with HTTP 502 / `ToolCallFailed` and `McpError` without a message. The failed program did not return the first diagnostic payload.
- One NVIDIA-only call to recover that omitted payload failed in 30,081 ms with the same error. No further live retry was made.
- Direct read-only SSH with batch mode, existing strict host-key checking, and a five-second connection timeout could not resolve `dakota-cpu`. No trust entry, credential, or connection configuration was created.
- CPU CLI version, daemon version, configuration flags, binary hash, and model tools therefore remain unavailable. The registry read and first successful NVIDIA call do not establish healthy native routes on both targets, and these observations do not establish the cause of the historical 502 incident.

## Exact native protocol and upstream source

The installed `0.166.0` CLI generated its experimental app-server JSON schema with an empty task-local `CODEX_HOME`. `ClientRequest.json` contains 179 request method literals and no method containing `board`. Its SHA-256 is `0fedfae5919181b6cdf532b0d155813522418c2c1a93214df6760bf6bd77123a`. The generated request schema includes `turn/steer`, `thread/inject_items`, and `thread/queue/*`, but no supported method to attach independent top-level roots to a shared native board.

Official tag `rust-v0.166.0` resolves through annotated tag object `593389380740f7b4762771573c447ccfdc59742f` to commit `6ff1e1ca3b30fc8b914720681fcf5fe62e95f856`. Relevant exact-tag sources:

- [`install_agent_message_board`](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/core/src/agent_message_board.rs#L60): automatic boards require both board and MAv2 feature gates, except for an explicit host-provided `LocalBoardConnection`.
- [`LocalBoardHost::actor`](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/core/src/agent_message_board.rs#L139): ordinary local actors must match the board's session/tree identity; another board is rejected. Host-connected boards use separate explicit connection ownership.
- [`LocalBoardHost::notify`](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/core/src/agent_message_board.rs#L243): notifications do not start/restore recipients; inter-agent notification creation uses `trigger_turn=false`.
- [`ClientRequest::ThreadInjectItems`](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/app-server-protocol/src/protocol/common.rs#L910): describes history injection without starting a user turn and uses per-thread request serialization.

Conclusion: `native_board_unavailable_for_independent_roots` is the truthful app-server capability for this installed version. Enabled feature, model-facing tool, explicit host-connected board, and supported JSON-RPC are separate facts. No board RPC should be invented and no SQLite writes are needed.

## Important fallback semantics

### Item injection cannot enforce idle-only delivery

- [`thread_inject_items_response_inner`](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/app-server/src/request_processors/turn_processor.rs#L976) loads an already managed thread, checks direct-input authority, validates response items, then delegates to core injection.
- [`load_thread`](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/app-server/src/request_processors/turn_processor.rs#L372) uses the loaded thread manager; an unloaded thread fails as not found rather than being implicitly resumed.
- [`ensure_direct_input_allowed`](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/app-server/src/request_processors/thread_input.rs#L23) rejects direct input into MAv2 spawned subagents; it is not an idle-state check.
- [`CodexThread::inject_response_items`](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/core/src/codex_thread.rs#L907) does not start a new turn.
- [`Session::inject_client_response_items`](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/core/src/session/inject.rs#L74) atomically queues input into an existing nonfinishing active turn; otherwise it records history. It does not reject a turn that becomes active after a CAS pre-read.

Therefore a CAS idle pre-read cannot provide an atomic idle-only injection guarantee. Do not build a relay that promises that guarantee using `thread/inject_items`.

### Queue add can wake a recipient

- [`ThreadQueueRequestProcessor::add`](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/app-server/src/request_processors/thread_queue_processor.rs#L77) delegates to the native queue service.
- [`QueuedItemService::enqueue`](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/ext/queue/src/service.rs#L265) persists the item, emits the change, then calls `wake_if_loaded`.
- [`QueuedItemService::wake_if_loaded`](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/ext/queue/src/service.rs#L472) emits an idle lifecycle event for a loaded noninterrupted recipient; this event can dispatch queued input.
- [`QueuedItemService::dispatch_if_idle`](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/ext/queue/src/service.rs#L405) starts queued user input with `start_turn_if_idle`.
- [`QueuedItemService::on_thread_idle`](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/ext/queue/src/service.rs#L549) can dispatch the pending item when an active turn becomes idle.

Therefore queue/add alone is not a passive mailbox and violates the issue's no-auto-waking relay contract. A guarded `turn/steer` relay for an already active exact turn plus an explicitly external folder fallback for idle/unloaded workers is the bounded supported choice.

## Acceptance still unproven

No independent live thread pair, live exchange/read acknowledgement, cross-target/unknown-CWD negative live case, idle/no-recipient live case, or reboot test was run. Fixture checks can verify the new CAS boundary but cannot close these live acceptance requirements. No inference of delivery/read should follow native mutation acceptance alone.

## CAS implementation verification

The relay was tested through a disposable Unix WebSocket protocol fixture with two independent thread identities and exactly two steer effects (message and reply); reconnect returned the saved receipt. Cross-target/CWD/pair-policy, idle/unloaded/unknown state, mismatched native acknowledgement and unknown-transport cases are covered locally.

Pinned `action-server devenv task test`: 815 passed, 18 skipped. Skips require the optional worker image (1), production image proof (6), and serialized package-composition runtime acceptance (11). `action-server devenv task lint` and `git diff --check` passed. Independent source review findings for effective deployment exposure and handshake sanitization were corrected with failing-before/passing-after regressions. These checks do not satisfy the live gates above.

The coordination PR is stacked on maintenance PR #26 at `a657ac07329f617305098f06847872bf38043193`. The generated method catalog and synthetic receipt fixture were refreshed for the new action ownership/regression map. The native RPC method set is unchanged; the synthetic fixture is wire-test evidence only.
