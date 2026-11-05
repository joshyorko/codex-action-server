# Independent thread coordination

CAS does not expose a native message-board RPC or attach independent roots to a
shared board. `list_native_capabilities` reports
`coordination.status = native_board_unavailable_for_independent_roots`.
That describes CAS's supported surface; model-visible tools and host feature
flags remain explicitly unverified. See the [exact-version acceptance
record](acceptance/coordination-0166.md).

## Active-turn operator relay

`relay_coordination_message` is a consequential operator/control action.
Observe deployments cannot invoke it. It uses only existing `thread/read` and
`turn/steer` mappings, leaving native method exposure and qualification intact.
It never starts, resumes or interrupts a turn.

An operator opts in with an absolute `CODEX_ACTION_COORDINATION_POLICY` path.
The policy must be a regular non-symlink file owned by the CAS user with no
group/other access, at most 64 KiB. Its only field is `pairs`, a list of at
most 64 directional entries. Each entry contains `sender` and `recipient`
objects with exactly `target`, `cwd` and `thread_id`. Both peers must use the
same configured logical target. Enroll both directions only if replies are
authorized. Keep this file outside source checkouts and mount it read-only.
CAS does not create or edit this policy.

The action payload identifies the recipient with `target`, `cwd` and
`thread_id`, and supplies the exact `sender` object, `mode: "steer"`,
`expected_turn_id`, a unique `request_id`, and `text`. Optional
`in_reply_to` labels a reply. Text must contain 1–2048 UTF-8 bytes; the complete
native frame is limited to 4096 bytes.

The caller acts as the authenticated operator. Sender identity is context
checked against native state and the pair policy, **not authenticated agent
authorship**. The native message labels this distinction and treats its body
as untrusted data. Never relay secrets or authority-expanding instructions.

CAS verifies both stored thread IDs/CWDs and target identity, then requires an
active recipient with no reported approval/input flags. Native
`expectedTurnId` binds steering to the exact turn if it finishes between the
read and mutation. Idle, unloaded, missing, erroneous or unknown recipients
refuse delivery. There is no automatic alternate transport.

A durable reservation precedes native connection. Repeating the same request
returns its receipt without another dispatch; changing its payload under the
same key fails closed. Reconcile unknown acknowledgements using
`read_dispatch_receipt` and native state; do not retry with a fresh key.
Native acceptance proves neither model delivery nor worker read.
`in_reply_to` is an operator label, not proof of an agent acknowledgement.
Receipts contain identifiers/fingerprints, never message text.

Item injection is excluded because it can race into an active turn after an
idle read. Queue-add is excluded because `wake_if_loaded` can start a turn.
Neither provides the idle-only, no-wakeup contract required here.

## Opt-in folder fallback

This is an external filesystem mailbox, **not the native Codex message board**.
Workers need an explicitly authorized shared filesystem on the same target.
Idle Codex does not poll or wake itself. No daemon or network bus is needed.

1. The root owner creates a mode-0700 run directory outside every source
   checkout, for example `~/.local/state/cas-coordination/<run-id>`. Record a
   roster binding worker names to exact target/root-thread/CWD identities.
   Assign each worker its own `outbox/<worker>` and `receipts/<worker>`
   namespaces. This is a same-user trust boundary, not isolation between
   processes using the same Unix account.
2. Root-owner instructions specify each worker's namespace, authorized peers
   and bounded task. Messages are data, never permission to execute arbitrary
   commands, edit another worker's source or expand scope. Never include
   credentials, secrets or private transcripts.
3. Use immutable unique message IDs, exact sender/recipient roster identities,
   UTC creation/expiry times and at most 2048 UTF-8 bytes of text. Write a
   mode-0600 temporary file in the sender's namespace, flush/fsync it, publish
   atomically without replacing an existing ID (for example, hard-link to the
   final unique name), fsync the directory, then remove the temporary name.
   Reject symlinks, malformed, oversized, expired or wrong-run messages.
4. During an already authorized turn, poll at most every two seconds for at
   most 30 checks; stop on acknowledgement or deadline. Record each ID
   durably before any requested effect. After a crash, reconcile uncertain
   effects; missing acknowledgements never authorize execution again.
5. The recipient writes an immutable acknowledgement in its own namespace
   with the original ID and bounded outcome, using the same atomic protocol.
   Receipt and completed work are separate facts. After restart/reboot, read
   prior IDs/receipts before acting; never create a turn automatically.
   Missing or idle recipients produce a bounded timeout, not success.
6. Limit TTL to 24 hours and outstanding messages to 100 per worker. The root
   owner reviews receipts and removes only acknowledged, expired payloads for
   that exact run. Keep receipt/tombstone IDs until all workers stop and the
   run is explicitly retired. Expiry must not authorize duplicate work.
   Cleanup is operator-owned; never recursively remove a caller-supplied path.

## Acceptance

Local fixtures cover two-peer message/reply, exact identities and private pair
authority, state refusal, turn-ID mismatch, transport ambiguity and durable
replay fencing. Package/profile checks enforce observer denial.

Live acceptance still requires two disposable independent roots on the same
Dakota target, exact CWDs/IDs, explicit pair enrollment, bounded message/reply,
wrong-target/CWD negatives and absent/idle recipients. Retain receipts through
a separately approved reboot and verify no duplicate effects. Preserve the
primary Actions release thread; it must never become an acceptance fixture.
Fixture evidence does not prove live Dakota, ChatGPT or reboot acceptance.
