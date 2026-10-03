---
name: provision-remote
description: Create or resume an explicitly authorized Devsy native Codex worker using the standalone recipe.
---

Read docs/REMOTE_WORKERS.md in this repository before acting. This repository owns
.devcontainer/remote-worker/devcontainer.json and scripts/remote/setup.sh and
verify.sh. Devsy owns lifecycle. One central Codex Action Server controls local
and remote native Codex daemons; never install a second central API in the worker.

Discover provider_list and workspace_list. Reconcile exact source/context/provider
before workspace_create or workspace_start. Zero matches permits creation only
when authorized; multiple matches fail closed. After uncertainty, list/status
before retry; never blindly replay creation. Use workspace_status and bounded
workspace_exec for runtime verification, not TTY authentication. Operator auth
and a separately authorized canary are distinct gates. Do not print credentials
or device codes. Source-selected central targets resolve fresh identity per call.

Keep workload cwd/thread/turn identity together. Do not resume unrelated parked
threads or create replacement swarms. Report source SHA, tool versions, workspace
identity, native daemon readiness, authentication status, and exactly which
read-only acceptance checks actually passed. Static tests are not hosted proof.
