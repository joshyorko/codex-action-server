# Disposable native Codex workers

## Ownership and topology

This repository owns the reusable worker recipe. Friday is a client of the
central typed API and does not install, bootstrap, or own remote workers.

Dakota runs one central Codex Action Server. Its logical targets reach the local
native Codex daemon and remote native Codex daemons. A remote worker runs native
`codex app-server` only: do not start another Actions Runtime, tunnel, Hermes
profile, scheduler, or control database inside it.

Devsy owns workspace lifecycle, provider, source/image, IDE, readiness, and retry
semantics. The central API owns logical target resolution and typed Codex RPC.
Provisioning is an explicit operator/client workflow through Devsy's native MCP
or CLI; it is not a new Action Server tool that creates arbitrary machines.

## Recipe

Use source `git:https://github.com/joshyorko/codex-action-server.git` with
`.devcontainer/remote-worker/devcontainer.json`. The source checkout must contain
the reviewed recipe commit; an unmerged recipe branch must be selected explicitly
using the installed Devsy version's supported source/ref configuration. Do not
claim main contains an unmerged PR. Verify `git rev-parse HEAD` inside the workspace.

The definition retains the pinned room-of-requirement secure image from Friday.
It does not rebuild or transfer ownership of that image. Its create hook runs
`scripts/remote/setup.sh`, which installs missing tools, initializes a fresh
Codex config, configures Headroom/RTK, and installs luna-factory. Its start hook
starts the native Codex daemon before running `scripts/remote/verify.sh`.
Verification never starts a daemon itself. No authentication is automated.

### Operator settings

Overrides use `CODEX_WORKER_`, not the former Friday-specific namespace:

- `CODEX_WORKER_CODEX_HOME` (or inherited `CODEX_HOME`), default `/home/vscode/.codex`
- `CODEX_WORKER_CODEX_BIN`, default `/home/vscode/.local/bin/codex`
- `CODEX_WORKER_CODEX_CONFIG`, if supplied, must equal the selected home/config.toml
- `CODEX_WORKER_BREW`, `GH_BIN`, `PYTHON`, `HEADROOM_BIN`, `RTK_BIN`, `CURL_BIN`
  (each prefixed with `CODEX_WORKER_`) select existing executable paths
- `CODEX_WORKER_HEADROOM_URL` selects the credential-free HTTP(S) proxy endpoint

For migration parity the default proxy remains `http://10.10.10.89/v1`. Other
operators must supply their reachable endpoint before creating the workspace.
The endpoint accepts hostname/IPv4, port, and path, not embedded credentials,
query parameters, shell syntax, or IPv6 literals. Existing config/provider/model
choices are preserved; changing this variable does not rewrite a retained config.

Fresh defaults preserve the existing Luna model/effort/context policy. Headroom
initialization selects its provider; RTK owns its native integration. The Brew
tap is `joshyorko/tools` (repository `joshyorko/homebrew-tools`), and the upstream
Codex installer is `https://chatgpt.com/codex/install.sh`. The image is digest-pinned,
but Brew formulas, installer, and plugin marketplace main are upstream-moving
inputs. Record installed versions and selected source SHA in acceptance evidence;
this recipe does not claim a bit-for-bit reproducible toolchain.

## Create or resume with Devsy

Discover the actual installed tool schemas before calling them. The native MCP
workflow is `provider_list`, `workspace_list`, `workspace_status`,
`workspace_create` or `workspace_start`, then bounded `workspace_exec`.

1. List workspaces in the selected Devsy context. Match the exact repository
   source and provider, not a historical generated hostname or the newest record.
2. Exactly one matching record: inspect its status. If stopped and resume is
   authorized, use native `workspace_start`. Never create a second workspace to
   work around an uncertain status result.
3. Zero matches: only with creation authorization, call `workspace_create` once
   with the approved name/provider/source/IDE and
   `devcontainer_path=.devcontainer/remote-worker/devcontainer.json`.
4. Multiple matches: report ambiguity and ask the operator to select a context
   or specific workspace. Do not silently pick one or delete extras.
5. If create/start times out, reconcile native `workspace_list` by exact source,
   context, and provider, then `workspace_status` for returned identities. Do not
   blindly replay creation. A workspace record does not prove a running container.
6. After readiness, bounded `workspace_exec` runs the recipe verifier and records
   source HEAD and tool versions. Do not run setup on an unrelated existing home.

A credential-free Git source is preferred. For local sources use the installed
MCP's documented `local:/absolute/path` syntax; do not assume CLI `.` syntax is
accepted by MCP. Image-only sources may bypass the repository definition and
cannot prove that this recipe ran. Devsy versions differ: inspect the installed
schema/help rather than copying unsupported CLI flags.

Authentication is a separate explicit operator CLI/SSH/PTY step. Never use
`workspace_exec` for TTY authentication. Never request, return, or persist tokens,
passwords, cookies, or device codes. The native Devsy MCP surface does not imply
interactive authentication, capacity admission, IDE catalog/open, task polling,
or an independent identity service.

## Bind the central logical target by source

Use `config/targets.example.json` as the shape, preserving the existing local
target. For a recreate-safe single remote pool:

```json
{"transport":"devsy","context":"default","provider":"kubernetes",
 "source":"https://github.com/joshyorko/codex-action-server.git",
 "user":"vscode","codex_bin":"/home/vscode/.local/bin/codex"}
```

For a nondefault `CODEX_WORKER_CODEX_HOME`, also supply the verified daemon
`socket_path` in this central target, for example:

```json
{"socket_path":"/workspaces/worker-state/codex/app-server-control/app-server-control.sock"}
```

Merge that field into the target object above. Obtain it from native daemon
version under the selected home. The bootstrap hook's environment does not set
the central SSH session's CODEX_HOME; omitting socket_path would discover the SSH
user's default home instead. Explicit sockets bypass that default-home discovery.
The path must exist in each recreated worker. Verify the native initialize reply's
Codex home after connection; a socket path alone is not proof of the right home.

Use the exact `source.gitRepository` value returned by authoritative Devsy JSON.
Do not carry a deleted workspace's `workspace`, `workspace_uid`, generated alias,
or port into a source-selected target. The resolver reads Devsy list/status on
each request, chooses exactly one source/context/provider match, obtains its
current UID and generated SSH configuration, and verifies UID/ID on the actual
remote connection. Thus names/IDs may change while client target `devsy` stays
stable. A selected old identity cannot silently reach a different current one.

Multiple remote pools use separate logical target names and unambiguous source,
context, or provider selectors. For intentional concurrent workers with the same
source/context/provider, use explicitly selected `workspace` + `workspace_uid`
per logical target; those pinned targets deliberately fail after recreation until
an operator updates them. The resolver never picks the latest matching workspace.

An existing native Devsy TCP/SSH route must already be established by the
operator's supported Devsy workflow. Resolution never starts that route and
rejects lifecycle-triggering ProxyCommand/ProxyJump. Credentials/authentication
and route availability are separate from container and daemon readiness.

## Acceptance, in order

1. Static/executable fixture tests: recipe and resolver contracts only
2. Live workspace: correct source HEAD, tools, running native daemon and socket
3. Operator auth completion: separate from workspace readiness
4. Central `list_targets`, `inspect_target`, `read_server_diagnostics`, then
   `discover_threads` and an existing exact-cwd `read_thread`
5. Only with explicit authorization, one bounded authenticated canary turn,
   tracked through exact thread/turn IDs to terminal state

Do not create threads to disguise empty discovery. Do not restart parked swarms
or change their provider/model configuration for acceptance. Hosted fixture tests
are not live Kubernetes, Headroom, authentication, or ChatGPT tunnel proof.

## Provider seam

Devsy is the only compute lifecycle supported by this recipe today. Keep its
native lifecycle outside typed Codex actions. A future Podman/Docker lifecycle
adapter must supply an operator-owned workspace identity and an already-running
native Codex endpoint; it can then use an explicit logical-target resolver
transport. No future provider implementation, generic remote shell API, job
registry, or second scheduler is added by this port.

## Provenance

Ported from Friday main c58c9e1 and the retained-config/readiness improvements in
Friday PR43 fc93b4a. Friday PR45 becomes consumer-only; its retired provisioning
entrypoint links here. Static and executable recipe tests moved with the code.
No live machine or existing daemon is changed by moving these repository files.
