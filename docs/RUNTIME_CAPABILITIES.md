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

## Native tool metadata boundary

`actions.mcp.tool` from Core 1.0.1 accepts MCP read-only, destructive,
idempotent, and open-world hints directly; a probe against the hash-verified
Runtime 1.0.2 adapter source
(`d8d8cf0914419c3e2037b81339d089b3cbdc1900f7dd3027dc8751d19ac73898`)
confirmed they reach its unpatched catalog. The ordinary `actions.action`
decorator used by CAS carries `is_consequential`, but that flag does not create
MCP behavioral hints. The native tool decorator registers kind `tool` and omits
`is_consequential`; stacking both decorators would register the endpoint twice.

Therefore the probe does not justify replacing CAS decorators. Preserve action
kind and HTTP/OpenAPI consequential metadata, schemas, and profile behavior
across both the host binary 1.0.1 and container Runtime 1.0.2 before removing
the annotation patch. CAS retains ordinary `@action` wrappers and derives
`is_consequential` from `src/action_catalog_contract.py:CAPABILITIES`.
Compatibility and assembled split packages retain the exact entrypoint file
`src/codex_actions.py`. `scripts/install_runtime_annotation_patch.py` serializes
reviewed package/file/action tuples for `codex-action-server`, `codex-observe`,
and `codex-control`; unrelated packages and files are unchanged. A capability
in the wrong split package receives conservative hints rather than read-only
classification. The transport patch is independent and remains required.

Source evidence is Core `actions.mcp.tool` and the Runtime adapter's
`McpServerSetupHelper.register_action`, alongside CAS
`scripts/install_runtime_annotation_patch.py:annotation_options`. The probe
verified decorator registration and adapter catalog hints. It does not prove a
native `@tool` migration retains HTTP/OpenAPI consequential metadata. The
unpatched native CLI 1.0.1 does not receive the wheel's annotation projection.
Keep host-binary and wheel/container proof separate.

## CAS package composition

One Runtime can serve both split packages through additive imports followed
by `--actions-sync=false`. CAS implements that opt-in startup path in
`scripts/start_packages.py:start`. It retains the aggregate compatibility
package as the default and applies an exact package/action whitelist to both
startup paths. Native MCP names remain unqualified and globally unique. HTTP
routes include the package name.

Source groups, package selection, and client authorization are separate.
Shared package environments and Runtime process-pool keys provide execution
ownership, not security isolation. Runtime 1.0.2 keys workers by package,
environment, and directory and gates running actions with a global
`max_processes` semaphore. CAS retains `--min-processes 1 --max-processes 1`.
See [PACKAGE_COMPOSITION.md](PACKAGE_COMPOSITION.md) for the selection
contract and acceptance evidence. Runtime deployment/projection/client-auth
extensions remain tracked in
[Actions #129](https://github.com/joshyorko/actions/issues/129),
[Actions #130](https://github.com/joshyorko/actions/issues/130), and
[Actions #91](https://github.com/joshyorko/actions/issues/91).
