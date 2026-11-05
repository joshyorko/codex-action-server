# Independent thread coordination

Spec: GitHub issue #28. CAS owns only typed operator mediation; native Codex
owns thread/turn state. No board RPC, SQLite writes, wakeups, host services,
credential changes, primary Actions thread mutations, merge or deployment.

- [x] Inventory installed CLI, daemon, feature flags and generated protocol.
  Keep model-tool availability and independently shared boards unverified unless
  exercised; record unavailable targets explicitly.
- [x] Add a coordination capability to the existing native capability report.
  Add one consequential operator-only relay action, using existing
  thread/read and turn/steer mappings, with an explicit
  private operator pair allowlist. Keep the native method allowlist unchanged.
- [x] Verify sender and recipient target/CWD/thread identities, mode and native
  state before mutation. Bound messages to 2 KiB, label operator attribution,
  and reserve durable dispatch IDs. Never replay an ambiguous acknowledgement.
- [x] Prove allowed message/ack round trips and denied target/CWD/pair/state
  cases with disposable native fixtures. Preserve observer and split packages.
- [x] Document an opt-in external folder mailbox with atomic per-worker files,
  explicit acknowledgement, bounded polling, TTL and owner cleanup.
- [x] Run repository tests/lint and source review; prepare one draft PR. Record
  live Dakota/reboot acceptance separately without changing host configuration.

Source review decision: exclude inject_items because its idle check cannot be
made atomic from CAS; exclude queue/add because it calls wake_if_loaded and
can start a turn. Relay only to an explicitly named active turn. Idle workers
need the external folder fallback.

Live Dakota exchange and approved-reboot acceptance remain blocked/unrun; CPU inventory is unavailable. No merge or deployment is authorized by this plan.
