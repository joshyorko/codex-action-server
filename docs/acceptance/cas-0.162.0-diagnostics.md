# Codex 0.162.0 diagnostics compatibility

The exact `0.162.0` native build is admitted for `server/diagnostics` only.
Other experimental methods, future patch versions, prereleases, and build
metadata variants retain their existing fail-closed version checks.

The installed native binary reported `codex-cli 0.162.0` and SHA-256
`50ed828f357c655a3c82054d346cab8434f24901f14ec19b8267571bdd008b38`.
Its `app-server generate-json-schema --experimental` command generated the
reviewed schema in a private temporary directory with isolated `CODEX_HOME`
and cache paths. No native daemon restart or configuration change was required.

The generated request takes an empty object. The response requires `process`
and `gauges`: process ID is an integer, memory fields are nullable integers,
and each gauge contains a string name and integer value. These fields match
the existing CAS diagnostics contract.

The retained [schema fixture](../../tests/fixtures/protocol/codex_0.162.0_server_diagnostics.json)
contains the generated request variant, complete diagnostics parameter and
response schemas, native binary digest, and generated-file digests.
[Regression tests](../../tests/test_native_diagnostics_0162.py) verify the
consumed shapes, exact-version admission, and rejection before send for every
other experimental method and unreviewed versions. This review does not claim
authentication readiness, queue compatibility, or broad 0.162.0 compatibility.

The candidate gate was also applied in a separate, bounded read-only probe
process inside the existing CAS controller. After resolving and checking the
workspace UID, the client initialized against the running native `0.162.0`
daemon and successfully requested `server/diagnostics`. Its response contained
the expected process object and one correctly typed gauge. The probe changed
no running CAS process or native daemon and did not deploy this source patch.
## Version drift and metric boundary

An installed build outside the exact reviewed-version table is refused locally
before `server/diagnostics` is sent. The public error identifies
`experimental_native_version_not_qualified`, the method, observed version (or
`unknown`), and versions qualified for that method; nonexperimental reads remain
available. Provider acceptance now attempts diagnostics for every native build
instead of silently skipping builds other than 0.160.1, and records an
unqualified result separately from a transport or protocol failure.

`server/diagnostics` reports the Codex app-server process and its gauges. It is
not a whole-host CPU or RAM utilization API; use a separately authorized,
read-only host monitoring source for machine-wide resource utilization.

The [qualification run](https://github.com/joshyorko/codex-action-server/actions/runs/41061213575)
generated schemas for the exact `rust-v0.166.0` artifact; its
`server/diagnostics` schema matched the reviewed 0.162.1 schema. Provenance
records binary and asset digests, but `live_protocol_smoke` is `not_run`; 0.166.0
remains unqualified. Dakota CPU/NVIDIA probes and the Executor 502 reproduction
were not performed.
