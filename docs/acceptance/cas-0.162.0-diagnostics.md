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
