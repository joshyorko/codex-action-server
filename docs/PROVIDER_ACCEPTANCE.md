# Real-provider acceptance with Dagger

The separate `real-provider-acceptance` workflow tests Docker and rootless Podman
on disposable Ubuntu GitHub runners. It runs the same portable Python Dagger
pipeline available to operators. The fast fixture suite remains separate and
cannot establish real engine or native daemon readiness.

Dagger owns the test environment. The provider under test still creates real
workers through the selected Docker or Podman engine. An explicitly selected
host Unix socket enters the test controller, never a worker. Docker uses Dagger's
Unix socket forwarding. Podman uses the direct socket route described below.
The worker uses the existing digest-pinned remote-worker image and repository
create/start hooks. There is no additional worker image build or image publish.

## Run it yourself

Use a disposable Linux host or a dedicated engine account. Access to a Docker
socket grants control over that engine and can grant root-level host access.
Rootless Podman limits that authority to its owning user's containers. Dagger
itself needs its own supported engine runtime. In CI, Dagger runs on Docker for
both matrix entries; the Podman entry forwards a separate rootless Podman socket.

Install Dagger CLI `v0.21.10`, Python 3.12+, and the selected engine CLI on the
host. The Python SDK is pinned to the matching `0.21.10` release. From a clean,
committed checkout:

```sh
python -m pip install -r ci/requirements.txt
# Docker example. Choose the actual socket; no engine context is inferred.
dagger run python ci/dagger_acceptance.py \
  --engine docker --socket /var/run/docker.sock \
  --output /tmp/provider-acceptance-docker.json

# Rootless Podman example, after starting the user's Podman socket service.
dagger run python ci/dagger_acceptance.py \
  --engine podman --socket "$XDG_RUNTIME_DIR/podman/podman.sock" \
  --controller-socket /var/run/docker.sock \
  --output /tmp/provider-acceptance-podman.json
```

Each invocation uses a unique owner scope unless `--owner` is supplied. Do not
reuse that scope for any other work. The pipeline refuses a pre-existing worker
in the selected scope and skips automatic cleanup for that unowned scope. Output belongs
outside the checkout so it does not make the source dirty.

The driver shallow-fetches the selected local commit into a fresh temporary Git
repository. This preserves the actual source SHA while omitting the source
checkout's Git configuration, credentials, ignored files, and uncommitted files.
The provider then archives that exact commit into the worker. Git metadata does
not enter the worker.

## What a passing result establishes

- Actual creation from the reviewed recipe image and the supplied commit
- A running native daemon reached through the central logical-target resolver
  and the native `Client`, including a matching initialize `codexHome`
- Successful read-only `thread/list` call
- `server/diagnostics` is required and schema-checked when the native user agent
  is exactly `codex-cli 0.160.1`; on other versions, the report records an
  explicit skipped status, the observed user agent, and the pin-mismatch reason.
  A skip is not recorded as a diagnostics pass
- Stop/start preserves the same full container ID, Codex home, and config hash
- Start does not rerun the create hook. The test replaces the setup script with
  a failure sentinel only inside its disposable worker before stopping it
- Deletion followed by recreation produces a new full ID
- An old ID cannot stop the replacement, and an old resolved RPC target cannot
  reconnect to it. Fresh resolution reaches the replacement
- No host mounts or privileged access in the worker
- A harmless process on the runner host survives the worker lifecycle
- Scoped cleanup leaves no selected worker behind

The report records the source SHA, image reference and ID, engine version,
installed recipe tool versions, native user agent, diagnostics status, lifecycle
identities, stage, and cleanup result. It stores counts rather than native
thread or diagnostic content. If diagnostics is attempted for the exact pin and
fails, the failure report preserves the user agent and sanitized failure class.

The test uses the deliberately unreachable, credential-free Headroom address
`http://127.0.0.1:9/v1`. It never signs in, creates threads, starts model turns,
or uses a live account. A passing result proves provider and native read-only
control, not authentication, inference, Headroom availability, Devsy, Kubernetes,
or tunnel reachability.

## Podman test-controller route

Real CI identified an exec-completion failure through Dagger 0.21.10's forwarded
Unix socket. Podman completed `mkdir`, but its client waited until timeout.
On the same container, `/bin/true` timed out through Dagger and succeeded through
the direct host socket. The result persisted with matching Podman 4.9.3 client
and server versions. This is a test-transport limitation, not a passing result.

For Podman, Dagger still builds and caches the complete test-controller image,
including the verified static 4.9.3 client and committed source. The driver
exports Docker-media-type image data, loads it through the explicitly selected
local Docker socket, and runs an exact-ID disposable controller. Its only bind
mount is the selected Podman socket. It runs the unchanged full acceptance
program against actual rootless Podman workers, copies the JSON result using
the exact controller ID, and removes that controller in `finally`. No worker
image is rebuilt or published, and worker containers remain mount-free.

Both controller and worker commands have bounded timeouts. An uncertain
controller create is reconciled by its random run name and label before cleanup
uses the verified full ID. The local controller image is retained as a cache;
the driver never force-removes images that another run might use. Docker's
acceptance route remains entirely within Dagger's forwarded-socket environment.

## Failure and cleanup

The inner runner writes a verdict even when a test fails, so Dagger can return
the evidence file. The outer driver exits nonzero unless that verdict is
`passed`, cleanup succeeds, and the host process survives. Registry, package,
installer, or native protocol failures remain failures; no fixture substitutes
for an unavailable service. GitHub uploads evidence with `if: always()`.

Normal cleanup reconciles `status`, validates the complete 64-character ID,
stops a running selected worker, and deletes that same ID through the provider.
The provider rechecks its owner/name labels and identity before each mutation.
It never runs engine-wide prune, broad name matching, or a blind create retry.
The outer host driver and a final workflow step also attempt scoped cleanup if
the Dagger execution fails. Force-killing a local run can still interrupt
cleanup. Reconcile using the exact `owner` recorded in its report:

```sh
python ci/dagger_acceptance.py --cleanup-only \
  --engine docker --socket /var/run/docker.sock --owner THE_RECORDED_OWNER
```

The inner live test has a 30-minute deadline. The Dagger build/query has a
35-minute timeout; a Podman controller already handed to its host thread remains
bounded by its 1900-second wait and per-command timeouts while cleanup finishes.
The GitHub job has a 45-minute deadline. Individual provider commands also have
timeouts. A failed or interrupted create is reconciled before
cleanup, never replayed automatically.

## Caching boundaries

The Dagger graph installs runner system packages and Python dependencies before
copying repository source. Those layers can be reused when only source changes.
APT uses named cache volumes with locked sharing; pip has its own named cache
volume. The Docker CLI comes from an explicitly pinned upstream image digest.

A fresh nonce is added only before the real-engine test execution. This matters:
external container lifecycle calls must run each time, even when their source
and arguments match a previous invocation. A cached successful test is not new
provider evidence.

Dagger layer and volume caches persist when the same Dagger engine is retained.
GitHub's Python setup action separately caches SDK downloads across workflow
runs. Fresh GitHub runners do not retain a Dagger engine cache. This workflow
does not claim cross-run Dagger caching, upload `/var/lib/dagger`, or require a
Dagger Cloud token. Supported cross-run Dagger caching would need a separately
configured persistent engine or authorized Dagger Cloud setup.

The selected provider's image store also reuses the recipe image during the
same run. Every recreated worker still gets a fresh home and runs the real
bootstrap. No cached authenticated home or prebootstrapped worker replaces that
acceptance step.

## References

- [Dagger release and matching SDK](https://github.com/dagger/dagger/releases/tag/v0.21.10)
- [Dagger host Unix sockets](https://docs.dagger.io/reference/api/host/)
- [Dagger container cache and socket APIs](https://docs.dagger.io/reference/api/container/)
- [Dagger for GitHub action](https://github.com/dagger/dagger-for-github/tree/v8.2.0)
- [Podman system service](https://docs.podman.io/en/latest/markdown/podman-system-service.1.html)
