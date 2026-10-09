# Per-worker execution policy

CAS `start_thread`, `create_thread_and_start_turn`, `resume_thread`, and
`start_turn` accept optional `approval_policy` and `sandbox` fields in `payload`.
Supported approval policies are `untrusted`, `on-request`, and `never`.
Supported sandbox modes are `read-only`, `workspace-write`, and `danger-full-access`.
Omit both fields to preserve native defaults or the existing thread settings.

For an explicitly authorized unrestricted worker, set `approval_policy: "never"`
and `sandbox: "danger-full-access"`, with its exact target and isolated worktree
cwd. Full access removes the native sandbox; this is a per-worker decision, not
an operator-wide default. Native operator restrictions remain authoritative.
Executor approvals for separate tools are unchanged.

Thread start/resume maps to native `approvalPolicy` and `sandbox`. Turn start
maps to native `approvalPolicy` and the structured `sandboxPolicy` object.
Native turn overrides persist for subsequent turns. Create-and-start sets the
policy at thread creation and its first turn inherits that policy.

`effective_configuration.approval_policy` and `sandbox_policy` contain only
native response observations, never echoed request values. A native turn/start
response often has no effective settings; null means unobserved, not rejected.
Thread start/resume responses expose native observations, including policies
that differ from the requested settings due to operator restrictions.

These options do not modify an already active turn through steer or settings
update. Inspect a paused turn and its exact pending approval first. Resolve it,
or interrupt only that exact turn when authorized, before starting a continuation
on the same thread with the desired policy. Never duplicate a paused worker or
blindly replay its task. Persist a unique request ID before dispatch; reuse is
allowed only with identical arguments. A policy change requires a fresh ID.
