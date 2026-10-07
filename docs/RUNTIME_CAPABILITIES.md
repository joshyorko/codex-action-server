# Runtime capability audit

Audited 2026-10-07 against the immutable community source snapshot at
[joshyorko/actions commit `8bdce09944c9e370917a8222243cd1a239ea7060`](https://github.com/joshyorko/actions/tree/8bdce09944c9e370917a8222243cd1a239ea7060),
the published [`actions-runtime` 1.0.2](https://pypi.org/project/actions-runtime/1.0.2/)
wheel, and the [`mcp` 2.0.0](https://pypi.org/project/mcp/2.0.0/) SDK.

The container uses `actions-runtime==1.0.2`, `actions-core==1.0.1`, and
`mcp==2.0.0`. The native `action-server` release is separately versioned at
[1.0.1](https://github.com/joshyorko/actions/releases/tag/actions-runtime-1.0.1);
the host launcher and host-runtime CI still use that release. The CAS package
version (`0.1.1`) and CAS protocol contract version (`0.1.0`) are separate again.
These versions describe different artifacts and must not be substituted for one
another.

The published runtime adapter already supplies MCP structured tool results,
catalog revision metadata, request correlation, catalog replacement, and
standard prompts/resources behavior. CAS keeps its narrow annotations in the
version- and source-hash-guarded container patch documented in
[CONTAINERS.md](CONTAINERS.md); read-only actions also advertise idempotency,
while the runtime's conservative `openWorldHint=true` default remains in
effect.

The pinned 1.0.2 adapter was exercised through the real `mcp==2.0.0` SDK and an
in-process ASGI client. That proved the projected read/control/unknown hints,
`openWorldHint`, `_meta`, catalog revision, and structured result on the
published adapter. The check used a synthetic action executor; it is not a full
container-image test, a native daemon acceptance run, or live host-runtime
proof.
