# Codex package composition implementation plan

> For agentic workers: use isolated worktrees and parallel independent ownership; integrate before serialized acceptance.

**Goal:** Ship source-owned capability metadata and independently selectable observe/control packages while retaining the root compatibility package.

**Architecture:** One capability record per stable action name, one undecorated shared implementation, and three mutually exclusive deployment choices: compatibility, either split package, or both split packages. One existing Actions Runtime serves the selected packages; package selection does not authorize clients.

**Tech stack:** Python 3.12, Core 1.0.1, container Runtime 1.0.2/MCP 2.0.0, native CLI 1.0.1.

**Spec:** `docs/PACKAGE_COMPOSITION.md`, with runtime and native boundaries in `docs/RUNTIME_CAPABILITIES.md` and `docs/NATIVE_CAPABILITIES.md`.

## Constraints and agreed interfaces

- Preserve all 64 names, typed schemas, Response envelopes, error codes, native version gates, operator targets and durable receipts. No merge, live deployment/restart, ADMIN or callback expansion.
- `src/action_catalog_contract.py` owns frozen capability records and `CAPABILITIES`, existing `ACTION_NAMES_BY_PROFILE`, and `PACKAGE_ACTION_NAMES` for `codex-action-server`, `codex-observe`, `codex-control`.
- `selected_package_names() -> tuple[str, ...]` reads `CODEX_ACTION_PACKAGES`, defaulting to `codex-action-server`. Reject unknown, empty, duplicate or aggregate-plus-split selections. Observe profile rejects explicitly selected control packages.
- `action_names_for_deployment(profile: str | None = None, packages: tuple[str, ...] | None = None) -> frozenset[str]` intersects validated package composition and profile. Preserve `action_names_for_profile` as the profile-only interface.
- `native_methods_for_deployment(...) -> frozenset[str]` uses explicit potentially composite capability mappings. Inventory and health reflect the served composition.
- `src/capability_registration.py:action(*, package: str = 'codex-action-server')` derives consequential behavior from capability records, registers only selected/profile-allowed endpoints, and denies excluded direct calls. Keep ordinary action kind until native tool/HTTP parity passes.
- Compatibility endpoints stay at `src/codex_actions.py`. Split endpoints are generated from compatibility signatures and delegate to the same undecorated functions in `src/codex_shared/`; each assembled package uses `src/codex_actions.py` so its registration/patch provenance is predictable.
- `scripts/assemble_packages.py --output ABSOLUTE_DIRECTORY` produces self-contained `codex-observe/` and `codex-control/` artifacts with v2 manifests and shared pinned source, without sibling PYTHONPATH dependencies. It validates generated membership against capability records.
- `scripts/run-packages.sh` uses explicit selected packages, additive imports into the configured datadir and `--actions-sync=false`, plus a package whitelist and exact catalog validation to exclude retained stale imports. Keep root compatibility startup as the default. Container selection follows the same contract.
- Annotation injection remains exact package/file/action scoped if native authoring cannot retain all HTTP/OpenAPI behavior; do not remove the independent transport patch. Derive annotation classification from capability records without importing source dependencies into the runtime process.
- One writer per file. Metadata owns registry, registration helper, inventory, annotation/health policies and their tests. Extraction owns compatibility/shared code and the test loader. Assembly owns manifests, pyproject, Containerfile, startup/assembly scripts and their tests. Acceptance owns composition integration tests and canonical docs.

## Review focus

- Importing shared implementations must register no endpoints; entrypoint functions must be discoverable/executable through the real CLI, not only Python imports.
- Aggregate plus either split package must fail; reused datadirs must not expose stale mutations.
- Observe denies mutations through Python, MCP and HTTP. Unknown profile/package selections fail before native dispatch.
- Package identity changes HTTP routes but not global MCP names. Root default consumers retain their routes.
- Shared environments/process pools are not security isolation. Benign dispatch through both packages and restart must preserve selected catalogs and durable receipts.

## Tasks

- [x] Metadata lane: add capability/selection regression tests first; implement explicit records, selection guards, registration, inventory and exact scoped annotation projection. Run focused profile/annotation/inventory checks.
- [x] Extraction lane: mechanically move models/helpers and coherent behavior groups into undecorated shared modules; retain typed compatibility wrappers and independent existing wire fixtures. Prove no shared import registration and preserve test transport injection without production proxy hacks.
- [x] Assembly lane: implement reproducible self-contained split artifacts and startup/whitelist gates; preserve root default and private state/network boundaries. Add lightweight assembly/startup tests before implementation.
- [x] Acceptance lane: inspect independent fixed catalogs and schema contracts; add actual CLI/MCP/HTTP/restart composition acceptance and update canonical guides with verified behavior and remaining limits.
- Integration verification requirement: integrate isolated commits, fix seams, run focused and full pytest/Ruff checks, then serialize native CLI and production image acceptance using disposable fixtures. Reuse unchanged evidence; report skips explicitly.
- Integration verification requirement: review final diff, push the exact resulting head to PR #10, refresh its description and monitor all triggered CI until green. Retain provider/live distinctions and documentation receipt.
