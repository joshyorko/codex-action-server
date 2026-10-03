# Local container worker provider

## Checkpoint 1: scope and architecture

Implementation is in progress. This checkpoint records the reviewed scope; it does not claim a working provider or runtime acceptance.

- Preserve the existing local, SSH, and first-class Devsy targets and native lifecycle.
- Add an explicit `container` target backed by Docker or rootless Podman on an operator-configured local Unix socket. No engine guessing or fallback.
- Use the repository's pinned worker image and its exact create/start hooks. Create disposable copied-source workers without host-home, engine-socket, or host PID mounts.
- Keep create/start/status/stop/delete in a separate operator CLI. Typed Codex actions never gain lifecycle or generic shell operations.
- Bind a stable operator owner + worker name to exactly one labelled container. Pin every connection and mutation to its full immutable container ID. Check labels, isolation, and native Codex home; fail on ambiguity, stale identity, and stopped workers.
- Keep one central Action Server. Worker connections run only native Codex daemon version/proxy through the selected engine.
- Add a portable Dagger integration test and separate GitHub Actions workflow that exercises the actual provider against a real engine. Do not substitute a Dagger-only worker for the provider path.

## Verification checkpoints

1. Current main 859f0af was inspected and materialized as a fresh isolated snapshot. No open PR overlapped this work when checked.
2. Baseline VM suite: 138 passed, 2 skipped because Actions Runtime CLI is unavailable. Docker and Podman are not installed in this VM.
3. In progress: dual-engine command/identity/lifecycle tests, preserving all Devsy regressions.
4. Pending: real GitHub-hosted engine/image/bootstrap/native-protocol acceptance through Dagger. No inference, credentials, or authentication changes.

Dagger v0.21.10 was the latest official release on 2026-10-03. The workflow will pin that release. Cache behavior and any cross-run credential requirement will be documented honestly.

## Scope fences

No Compose, Containerfile, GHCR publishing, Executor, tunnel wiring, live service restart, Review, Josh Room, or MemoryD changes. No merges.
