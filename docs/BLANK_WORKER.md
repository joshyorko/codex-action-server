# Blank Codex worker template

New workers use `image:ghcr.io/joshyorko/codex-action-server@sha256:<verified-digest>`.
The image is published from CAS main as `worker-sha-<source-commit>` and `worker-latest`.
Resolve and record the immutable digest before creating a workspace.

The image contains only the existing worker bootstrap scripts under
`/opt/codex-worker`, not a CAS checkout. Devcontainer image metadata runs setup
once and starts/verifies the native daemon on each start. The project directory
is `/workspaces`; clone the intended project there and use isolated worktrees.
No project repository can silently substitute its devcontainer for this runtime.

Authentication is deliberately separate: a new Codex home still needs login.
A running container or daemon does not establish authenticated CAS readiness.
Existing Git-source workers retain their recorded recipe; this template does not
move, erase, or recreate existing workspaces.
