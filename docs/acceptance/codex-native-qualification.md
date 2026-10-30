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
