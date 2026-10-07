# Container deployment

The image controls existing native Codex daemons. It installs the PyPI packages
`actions-runtime==1.0.2` and `actions-core==1.0.1`, Devsy 1.19.0, and an SSH client.
It does not contain Codex or start a native daemon. Runtime 1.0.2 publishes Linux
x86-64 wheels only, so this image
does not claim arm64 support.
The existing host launcher and host-runtime CI checks retain their separate 1.0.1
pin; this image does not upgrade the installed host runtime.

Production images are published to `ghcr.io/joshyorko/codex-action-server` as
`sha-<full-commit-SHA>`. Source tags starting with `v` retest and alias the already
published commit image. A release requires its immutable commit image to exist
first. Publication is serialized and refuses to overwrite a tag with different image content.
Use the digest reported by that job for deployment. Pull requests build and test
without registry authentication or publication. The focused
`feat/container-control-plane` branch can publish a tested candidate before merge.
These candidates are checkpoints, not release or runtime acceptance claims.

## Network and host dependencies

On Linux with rootful Docker and no UID remapping, the central API container
requires host networking to reuse Devsy's
existing loopback SSH routes. `CODEX_ACTION_BRIDGE_GATEWAY` must be an explicit
RFC1918 IPv4 address assigned to the managed Compose bridge. The container binds
only that address, normally `172.30.86.1:8088`. The Compose owner must verify the
gateway belongs to its project network and does not overlap another network.
This bounded image accepts port 8088 only.
Wildcard, public, hostname, IPv6, and loopback values fail startup. Do not publish
the API on a host LAN address. The existing `scripts/run.sh` host launcher retains
its loopback binding.

Executor and tunnel-client use their private Compose network and DNS. Executor
connects to the central API at the managed gateway. This repository owns the API
image; the tunnel kit owns Compose and the Executor connection.

Rootless Docker and user-namespace remapping are unsupported. Their network and
UID mappings do not preserve the operator's host loopback and private socket
permissions. Compose preflight must reject those modes.

The local target needs an explicit `socket_path` in its container configuration.
Resolve the operator's existing socket symlink on the host, then mount only its
resolved parent directory read-only at `/run/native-codex`. Set the local socket
path to the actual filename under that directory. The API connects directly to
that socket, without requiring or starting a Codex executable. Mounting only the
host's symlink directory does not expose its `/tmp` socket target. Match the daemon
owner's UID and private state ownership. Do not relabel the existing socket mount
or mount the whole Codex home, operator home, or Docker socket.

## Private configuration and persistent state

Mount operator-generated target JSON read-only at
`/run/codex-action-server/targets.json`. It must preserve the existing logical
targets and selectors, changing only the local socket path for the container.

Keep `/var/lib/codex-action-server` persistent and private. Its action directories
are `receipts`, `runtime`, and `actions`, respectively selected by
`CODEX_ACTION_RECEIPTS`, `CODEX_ACTION_DATA`, and `ACTIONS_HOME`. They must be owned
by the API UID with mode 0700. The image defaults to UID/GID 1000. Build-time
environment warming retains an immutable cache at `/opt/codex-action-server-cache`.
Startup copies only missing entries into the private writable `actions` cache,
including when an existing bind mount or named volume is attached. It never
overwrites cache entries or seeds a build-time database over retained history.
Reusing the same volume retains
receipts, the runtime database, and the package environment across restarts.

The same volume contains `robots`, selected by `ROBOTS_HOME`, and reserves
`launcher` behind the image's narrow `.actions` link. The PyPI runtime's RCC
executable remains in its baked Python package, so an empty state bind does not
hide that executable. The root filesystem remains read-only; no host home is mounted.

Deleting receipts removes dispatch replay protection. Never delete production
state as a restart or upgrade step. Network-shared filesystems are unsupported.

Use a read-only container filesystem, drop capabilities, enable
`no-new-privileges`, and provide bounded executable temporary storage at `/tmp`,
matching the validated fixture's 256 MB tmpfs.

The image sets the runtime's supported `SEMA4AI_OPTIMIZE_FOR_CONTAINER=1` mode.
It suppresses the ordinary startup API-key message without changing
authentication and uses RCC's live environment mode. The launcher never supplies
`--expose`, `--verbose`, or an API key on the command line. Its existing no-key
mode does not generate a key or enable authentication; private gateway access
remains the deployment boundary. Do not expose this API beyond that boundary.

## Existing Devsy targets

The image disables telemetry. Set `DEVSY_HOME` to the existing operator Devsy
configuration parent. Mount only its `config.yaml` file and selected
`contexts/default` directory read-only at their same absolute host paths.
Preserving these paths retains provider and generated SSH references. The
image's unused fallback is `/run/operator-devsy`. The context metadata must
include the selected workspace records and provider definitions; source-based
selection requires current workspace records rather than a stale copied inventory.

Each selected record also identifies its generated SSH configuration. Mount that
existing file, any included SSH configuration, and only its referenced identity
and known-host files read-only at the paths the configuration actually uses.
Do not mount the whole `.ssh` directory or forward an SSH agent. Kubernetes
providers additionally need their existing configured kubeconfig and any external
CA, client certificate, key, or credential-helper dependencies. Resolve these
references from operator metadata before adding individual read-only mounts.
Do not expose credential contents or start authentication.

The resolver still runs native Devsy inventory/status, selects exactly one running
workspace, uses its existing loopback route, and checks remote workspace identity
before native RPC. ProxyCommand and ProxyJump remain rejected. Missing context,
route, key, provider credential, or ambiguous source selection fails closed.
The image does not create, start, mutate, or reconnect Devsy workspaces.

No workspace records were present in the inspected host's default context during
container preparation. Its exact generated SSH/key mounts and live remote Codex
connection were therefore not tested. An empty inventory is not remote acceptance.
Keep CAS host-side if a provider cannot work with this narrow read-only contract.

## Verification

PyPI runtime 1.0.2 omits transport-security settings when constructing its MCP
adapter. The SDK therefore rejects a valid private gateway Host header. This image
applies a downstream fix to that owning adapter with
`scripts/install_runtime_transport_patch.py`. It requires the exact runtime 1.0.2,
SDK 2.0.0, and upstream adapter SHA-256 before applying. Unexpected inputs fail the
build. DNS rebinding protection remains enabled, with only the actual validated
IPv4 listener at port 8088 and its exact HTTP origin allowed. No wildcard,
Host-header spoofing, or alternate MCP server is used. This patch is pending an
upstream fix; remove it only after verifying an updated upstream runtime passes the
same valid-Host, foreign-Host, foreign-Origin, and loopback regression checks.

The image health command is
`python3 /opt/codex-action-server/scripts/container_health.py`. It initializes a
real MCP session, verifies the exact tool-name set for the implemented operator
profile, and closes the session. It never calls native Codex or Devsy. Healthy API
status does not prove a native connection, authentication, remote readiness, or
thread execution.

From the repository root on the Linux host, build with
`docker buildx build --platform linux/amd64 --load -f Containerfile -t codex-action-server:container-proof .`.
Then run
`CODEX_ACTION_CONTAINER_IMAGE=codex-action-server:container-proof .venv/bin/pytest -q -rs tests/test_container_runtime.py tests/test_container_image.py`.
The image proof creates only disposable fixture resources, checks the actual MCP
catalog and a real mounted Unix-socket fixture, then restarts the API and verifies
dispatch replay protection. It does not contact an operator daemon. Live local
native acceptance remains a separate operator gate in the composed stack.

The image projects the explicit operator policy in
`scripts/install_runtime_annotation_patch.py` through the pinned runtime's MCP
adapter. Read-only actions advertise `readOnlyHint=true`,
`destructiveHint=false`, and `idempotentHint=true`. Mutation/control and unknown
actions remain non-read-only, destructive, and non-idempotent. The runtime's
default `openWorldHint=true` remains in effect. Package, source-file,
runtime-version, and source-hash guards prevent the policy from changing foreign
tools. HTTP action kinds, schemas, authorization, receipts, and target checks
are unchanged. The older host CLI is not patched by this image-specific
correction. See [runtime capability audit](RUNTIME_CAPABILITIES.md) for version
boundaries and published-adapter evidence.
