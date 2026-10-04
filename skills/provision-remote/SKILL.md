---
name: provision-remote
description: Create or resume an explicitly authorized Devsy, Podman, or Docker native Codex worker using the standalone recipe.
---

Read docs/REMOTE_WORKERS.md in this repository before acting. This repository owns
.devcontainer/remote-worker/devcontainer.json and scripts/remote/setup.sh and
verify.sh. Lifecycle stays with the selected provider and explicit operator
commands. One central Codex Action Server controls local and remote native Codex daemons; never install a second central API in the worker.

For Devsy, discover provider_list and workspace_list. Reconcile exact source/context/provider
before workspace_create or workspace_start. Zero matches permits creation only
when authorized; multiple matches fail closed. After uncertainty, list/status
before retry; never blindly replay creation. Use workspace_status and bounded
workspace_exec for runtime verification, not TTY authentication. Operator auth
and a separately authorized canary are distinct gates. Do not print credentials
or device codes. Source-selected central targets resolve fresh identity per call.

For local containers, use scripts/remote/worker.py with an explicit engine, local
Unix endpoint, owner, and worker name. No automatic fallback or engine-service
setup. Reconcile status before creation; start/stop/delete require the expected
full container ID. Never reuse an unrelated owner scope, copy host credentials,
or introduce mounts to work around failures. Keep lifecycle outside typed RPC.
The container transport resolves the stable owner/name to an immutable ID and
checks native Codex home. Follow docs/PROVIDER_ACCEPTANCE.md for actual runtime
proof; fixture tests alone do not establish it.

Keep workload cwd/thread/turn identity together. Do not resume unrelated parked
threads or create replacement swarms. Report source SHA, tool versions, workspace
identity, native daemon readiness, authentication status, and exactly which
read-only acceptance checks actually passed. Static tests are not hosted proof.
