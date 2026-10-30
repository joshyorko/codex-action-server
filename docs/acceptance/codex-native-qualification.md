# Native Codex method qualification

## Approval source of truth

`ci/codex_qualification/approved_methods.json` records exact native-version and
method approvals, request/response schema SHA-256 fingerprints, and fixture
provenance. `ci/generate_native_qualification.py` verifies those fingerprints
against the retained source fixtures and generates
`src/codex_shared/native_qualification.py`. Runtime admission uses only that
generated exact-version/method table; unknown versions remain refused before
RPC transmission.

The repository test workflow runs the generator in check mode and exercises
the typed fail-closed regression tests. The scheduled and manually dispatched
`codex-version-qualification` workflow resolves a stable release from
`openai/codex`, verifies its immutable Linux asset digest, and runs its exact
binary in a credential-free disposable `HOME` and `CODEX_HOME`. It retains the
consumed diagnostics schemas, normalized comparison, binary/source provenance,
and a protocol-smoke receipt that contains only response-shape evidence.

Schema equality alone never admits a method. The candidate must also pass the
typed regressions and return the expected process/gauges shape from a disposable
native app-server. A compatible candidate produces a deduplicated review PR;
a changed schema or failed/unavailable qualification produces a deduplicated
blocked issue. Neither path auto-approves, merges, updates a daemon, or changes
an operator installation.

## Operational boundaries

`server/diagnostics` reports the Codex process and its gauges, not whole-host
CPU/RAM. The qualification workflow does not probe Dakota CPU or NVIDIA. Those
read-only post-deployment checks and the separate Executor 502 reproduction
require operational access and must remain independently evidenced; no host
mutation or daemon restart is part of this CI qualification.

## Candidate 0.166.0 evidence

Qualification run [41070538834](https://github.com/joshyorko/codex-action-server/actions/runs/41070538834)
verified the official release asset, matched the diagnostics schemas to 0.162.1,
passed typed regressions, and completed a disposable live `server/diagnostics`
smoke. The retained fixture and method-specific approval are in the source of
truth above. The pull-request run generated the candidate PR artifact; the
default-branch write job was skipped by design, so publication permissions and
idempotent PR updates remain unverified. Dakota device probes and the Executor
502 reproduction remain operational work, not CI qualification.
