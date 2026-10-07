# Select Codex action packages

CAS keeps one repository, one shared implementation, and one Actions Runtime.
The default deployment remains `codex-action-server`, with the existing HTTP
routes and all 64 operator actions. Split packages are an explicit opt-in.

## Select the catalog

`CODEX_ACTION_PACKAGES` is a comma-separated process environment setting.
`CODEX_ACTION_PROFILE` defaults to `operator`.

| Package selection | Operator profile | Observe profile |
| --- | --- | --- |
| `codex-action-server`, the default | 64 actions | 31 reads |
| `codex-observe` | 31 reads | 31 reads |
| `codex-control` | 33 controls | Rejected |
| `codex-observe,codex-control` | 64 actions | Rejected |

Unknown, empty, duplicate, or aggregate-plus-split selections fail startup.
Unknown profiles fail startup. Change either setting only with a full Runtime
restart. Package selection is not an action argument.

`src/action_catalog_contract.py:CAPABILITIES` owns package membership, source
groups, profiles, consequential metadata, behavioral hints, and composite
native method mappings. `action_names_for_deployment` computes the served
catalog. `src/capability_registration.py:action` registers allowed wrappers
and rejects excluded direct Python calls.

Source groups in `src/codex_shared/` contain undecorated implementation
functions. They do not register another package when imported. Public wrappers
retain their typed signatures at `src/codex_actions.py` in each package.
Assembled packages carry the same models, validation, native transport,
version gates, and durable receipt implementation.

## Start an opt-in composition

On the Linux host, configure the absolute operator-owned target JSON, private
receipt directory, and separate private Runtime datadir as described in
[README.md](../README.md). Then select packages and use the host launcher.

```sh
export CODEX_ACTION_PACKAGES=codex-observe,codex-control
export CODEX_ACTION_PROFILE=operator
bash scripts/run-packages.sh
```

`scripts/run.sh` uses the same selection logic. Neither launcher starts or
restarts a Codex or Devsy daemon. Both retain offline preflight and the
loopback bind. The existing container launcher applies the same composition
contract with its reviewed private bridge bind.

For split selections, `scripts/start_packages.py:start` imports every selected
package additively into `CODEX_ACTION_DATA`. It checks the effective catalog in
`server.db` for exact package/action membership, expected entrypoint file,
expected artifact directory, missing actions, and duplicates. It then starts
the Runtime with synchronization disabled and an exact package/action
whitelist. Automatic reload is not enabled.

Compatibility startup retains synchronized root discovery with the same exact
whitelist. Repeated synchronized starts of separate package directories are
not the split startup procedure. Synchronization can disable actions imported
from another directory.

When a datadir is reused, unselected records can remain in storage. The exact
whitelist excludes them from HTTP routes and the MCP catalog. Startup does not
delete retained Runtime state or dispatch receipts. Do not manually clear the
datadir to change selection.

## Assemble distributable packages

Run the assembler from the checkout with an absolute output directory.

```sh
.venv/bin/python scripts/assemble_packages.py --output /tmp/cas-action-packages
```

`scripts/assemble_packages.py:assemble` publishes complete `codex-observe/`
and `codex-control/` directories. Each contains a distinct spec-v2 manifest,
the complete pinned shared source, and only its selected decorated wrappers.
Neither needs a sibling checkout or host-installed CAS modules. The Linux
publication operation never replaces an existing destination directory.

The root manifest excludes `./tests/**` and `./scripts/**` from Runtime package
discovery and packaging. Core's recursive action-path finder recognizes
decorator text in development fixtures, so those checkout-only files must not
enter the public catalog. Development tasks still run from the source checkout.

The output root's `.cas-assembly.json` records the source fingerprint and
artifact file hashes. Identical repeated assembly reuses the artifact.
Changed source, changed artifact content, symlinks, or an unowned existing
output fail verification. Use a new output directory after source changes.

Set `CODEX_ACTION_PACKAGE_ROOT` to an absolute verified assembly output root
to use prebuilt artifacts. Without an override, checkout startup reuses a
verified baked `packages/` directory if present, otherwise it assembles under
`CODEX_ACTION_DATA/packages/<source fingerprint>`. The container bakes packages
at `/opt/codex-action-packages` and sets the override explicitly. Its prepared
build stage warms compatibility and both split package environments.

Split imports and startup use `CODEX_ACTION_DATA` as their working directory.
The Runtime stores package directories relative to that datadir and checks
their filesystem identity against the active working directory.

Split Runtime processes inherit `PYTHONDONTWRITEBYTECODE=1`. Core's logging
import hook respects that setting, preventing derivative Python bytecode
caches from changing the verified artifact tree. Fingerprint checks remain
strict across restart. A changed artifact is rejected, not cleaned or replaced.
If an earlier startup already created cache files inside an artifact, assemble
into a new absolute directory and select it with `CODEX_ACTION_PACKAGE_ROOT`.
Do not delete the existing operator artifact to make verification pass.

## Migrate HTTP consumers deliberately

MCP keeps the 64 existing unqualified action names. Packages cannot provide
duplicate tool aliases in the same catalog.

HTTP paths contain package names. For example, compatibility `read_thread`
uses `/api/actions/codex-action-server/read-thread/run`; the split package uses
`/api/actions/codex-observe/read-thread/run`. Split `start_thread` uses
`/api/actions/codex-control/start-thread/run`. Select the split deployment only
after updating consumers that use package-qualified HTTP routes. The default
compatibility deployment retains existing routes.

Both packages share the Runtime API credential, target configuration, and one
durable receipt directory. The observe profile is a server-wide registration
and direct-call guard. Separate client authority requires separately
configured endpoints or future runtime authorization. Package environments
and worker keys do not provide security isolation. CAS keeps one running
action at a time with `--min-processes 1 --max-processes 1`.

## Verify the delivery path

`tests/fixtures/package_composition_contract.json` fixes all 64 compatibility
signatures, payload schemas, and complete `Response` output-schema digests at baseline
`a731d2e48917561b6ee8d209e01e47802effe563`. The catalog expectation in
`tests/test_package_composition.py` fixes the independent 31/33 split.
Production metadata does not generate these test expectations.

The lightweight checks verify input and output schemas, signatures, package selection,
direct-call rejection, self-contained artifacts, and undecorated shared
modules. The protocol suite retains independent pinned native wire fixtures.

The actual CLI acceptance in `tests/test_package_composition.py` is opt-in
with `CAS_COMPOSITION_RUNTIME=1`. It uses a disposable Unix-socket native
fixture, private state directories, and loopback HTTP/MCP. It covers exact
catalogs, typed results, ordinary action kind, consequential OpenAPI metadata,
package-qualified routes, denied controls, inventory selection, alternating
dispatch between package worker keys, restart, changed selection in a reused
datadir, and receipt replay without another native dispatch.

`ACTION_SERVER_BIN` selects the CLI under test.
`CAS_COMPOSITION_RUNTIME_VERSION` defaults to the separate native release
`1.0.1`; set `1.0.2` for the pinned wheel CLI. Set
`CAS_COMPOSITION_ANNOTATIONS=1` only when testing the exact patched wheel or
container adapter. The unmodified host binary does not inherit that patch.
The annotation and transport patch purposes remain separate, as documented
in [RUNTIME_CAPABILITIES.md](RUNTIME_CAPABILITIES.md).

The native CLI's bootloader parent can exit before its actual server child
releases the datadir lock and listener. Acceptance checks the live ownership
of its dedicated fixture process group before each signal and waits for all
live members and the listener to stop. The next startup must not accept the
previous process's still-open port as readiness. Cleanup never targets an
unrelated service.

The completed local verification on 2026-10-07 records each delivery path
separately.

| Delivery path | Verified result | Boundary |
| --- | --- | --- |
| Unmodified native CLI 1.0.1 | 30 composition tests passed | Includes exact catalogs, HTTP/MCP, restart and changed selection, receipt replay, owned process cleanup, and conservative native hints |
| Patched wheel Runtime 1.0.2 with Core 1.0.1 and MCP 2.0.0 | 29 composition tests passed | Includes every deployment choice, projected behavioral hints, typed results, HTTP metadata, restart, and retained datadir selection |
| Built production image | 5 composition cases passed | Compatibility with both profiles, observe-only, control-only, and combined deployments; projected hints, typed results, HTTP routes/metadata, snapshots where available, non-root baked artifact readability, restart, and independent persisted receipt/replay checks |

The native receipt is `/tmp/cas-compose-native-acceptance-v2.log`, and the wheel
receipt is `/tmp/cas-compose-wheel-acceptance-v3.log`. These are local run
artifacts, not checked-in results. The image receipt is
`/tmp/cas-compose-image-acceptance.log`; it exercises the local
`codex-action-server:composition-proof` image built from source revision
`3dacad`, whose production inputs are unchanged by the later test/documentation
commits. This proves disposable image execution, not publication or a live
deployment. The wheel run used the same production
inputs before the later test-only native bootloader cleanup regression was
added. Native acceptance includes that regression. The unmodified native
catalog advertises `readOnlyHint=false`, `destructiveHint=true`,
`idempotentHint=false`, and `openWorldHint=true` for ordinary actions. The
patched wheel distinguishes the reviewed read/control classes. Matching
catalogs and schemas do not imply hint parity.

A skipped opt-in test is not runtime proof. None of these disposable fixtures
establishes live daemon, provider,
deployment, ADMIN, or retained callback acceptance. The required native tranche
and callback limits remain in [NATIVE_CAPABILITIES.md](NATIVE_CAPABILITIES.md).
