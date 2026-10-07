# CAS PR #10 Dakota production-readiness receipt

**Result:** `DEPLOY_READY=false`

**Observed:** 2026-10-07 on `dakota-nvidia`

**Worktree:** `/home/kdlocpanda/.codex/worktrees/cas-production-readiness`

**Branch / tested source:** `verify/cas-production-readiness` / `92b569bcaa953c2914a90bbd1ca42c2880832e8a`

**Merged PR:** [joshyorko/codex-action-server#10](https://github.com/joshyorko/codex-action-server/pull/10), merge SHA `92b569bcaa953c2914a90bbd1ca42c2880832e8a`

## Deployment identity

- Owner wrapper `scripts/control-plane-status`: CAS, Executor, and tunnel client were healthy; Host Devsy was ready.
- Running container `codex-control-plane-codex-action-server-1` was `running`. Its pinned `.Config.Image` and runtime `.Image` were `ghcr.io/joshyorko/codex-action-server@sha256:942f8f69a40ce729e5e66cfc244fedf6c8ddae6812955e10626bc762485f8bc2` and `sha256:942f8f69a40ce729e5e66cfc244fedf6c8ddae6812955e10626bc762485f8bc2`, respectively.
- `docker buildx imagetools inspect --raw` independently hashed the registry manifest to the expected `sha256:942f8f69a40ce729e5e66cfc244fedf6c8ddae6812955e10626bc762485f8bc2`. Media type: `application/vnd.docker.distribution.manifest.v2+json`.
- The manifest's **config digest is distinct**: `sha256:b3b654de83afd4f39e6d4459b5b116c4e8d23f68a12ebaabd672dfa94f40cee2`. The registry config and running container both advertise OCI revision `92b569bcaa953c2914a90bbd1ca42c2880832e8a`, Linux/amd64. Local and registry rootfs DiffIDs and revision labels matched. Docker's reported local image ID equals the manifest reference; it is recorded separately and is not called the config digest. Docker's local image-inspect config view had an additional `Healthcheck` field absent from `skopeo inspect --config`; every other listed config field matched. The pinned manifest reference is confirmed, while this engine-view metadata difference remains noted.
- **A1: MATCHED** to the requested published manifest and merge revision. No service lifecycle or deployment mutation was run.

## Diagnostics and native compatibility

- Central CAS `read_server_diagnostics({target:"local"})` returned the public error `server/diagnostics failed closed (RpcError); inspect target diagnostics`.
- The live InitializeResponse user agent was `codex-tui/0.161.0 (Linux Unknown; x86_64) ghostty/1.3.1 (friday-external-codex; 0.1.0)`. `codex app-server daemon version` also reported app-server `0.161.0`.
- CAS's native schema and experimental allow gate are pinned to `0.160.1` (`SCHEMA_VERSION` and `EXPERIMENTAL_NATIVE_VERSION`). `native_server_build_version` rejects this user agent as ambiguous because both `0.161.0 (` and `1.3.1 (` match the version/platform pattern. It returns `None`; `Client.request` then rejects `server/diagnostics` locally with `server/diagnostics requires the pinned experimental Codex 0.160.1 schema`. `_run` sanitizes that `RpcError` into the public error above. The native `server/diagnostics` method was **not sent**.
- This preserves the intended fail-closed policy: only the reviewed exact schema build may use experimental methods. Version `0.161.0` is not itself evidence of incompatibility, but no `0.161.0` schema contract or live compatibility proof is present here. No gate was weakened and no native/global configuration changed.
- Exposed schema inventory reports 104 default client requests, 167 experimental client requests, and 63 experimental-only requests. Queue actions and diagnostics are experimental and require exact `0.160.1`; ordinary typed thread/turn controls are not gated that way.
- **A2: BLOCKED BY INTENDED FAIL-CLOSED VERSION GATE. A3: COMPATIBILITY NOT ESTABLISHED for 0.161.0.** The ambiguous user-agent format also leaves inventory's `native_codex_version` as `unavailable`; resolving it safely requires a reviewed parser/schema contract change, not bypassing the gate.

## Disposable native control acceptance

All calls used the central Executor → CAS typed API with target `local` and cwd exactly the worktree above. Executor responses were unwrapped from `structuredContent` before inspecting each action's `result`/`error`. The rerunnable harness is [cas_pr10_dakota.codemode.js](../../scripts/acceptance/cas_pr10_dakota.codemode.js); it executes only the new thread it creates and deletes that exact ID in `finally`.

### Corrected-model run

- Executor run ID: `caspr10-muyji10v`; execution `completed`, `execution.ok=true`.
- Requested and observed first-turn model/effort: `gpt-6-luna` / `low`; observed provider `headroom`. First reply marker `CAS_LUNA_FIRST_TURN_OK`; native status `completed` in 2.505 seconds.
- Disposable thread: `01a117fa-f3ff-75a0-88a0-9d855899ce14`; name set to `CAS PR10 disposable caspr10-muyji10v`.
- First turn: `01a117fa-f495-7381-8c6a-2874cb13631b`.
- Queue request `caspr10-muyji10v-queue`: typed `add_thread_queue_item` returned dispatch `unknown`, `error_code=RpcError`, with no native-method receipt and no queued-submission ID. The exact-version gate rejected it before native dispatch; queue acceptance remains unproven.
- Second turn request `caspr10-muyji10v-turn`, turn ID `01a117fb-0c15-71c0-bac4-5905d910c2b2`: native `turn/start` returned `inProgress`; `turn/steer` for that exact ID succeeded; native `turn/interrupt` then returned successfully while it remained active. `thread/turns/list` reported terminal `interrupted`; `get_thread_snapshot` reported thread `idle`, latest turn `interrupted`, `native_state_authoritative=true`.
- Cleanup request `caspr10-muyji10v-delete`: native `thread/delete` dispatch `accepted`. Follow-up `read_thread` for that exact ID returned `thread/read failed closed (RpcError)`, confirming it was no longer readable.
- **A4: PARTIAL.** Real create, first turn, steering, status, terminal evidence, and cleanup passed. Queue mutation was blocked by the version gate. Diagnostics was separately blocked as above.

### Model correction and earlier disposable

Before the owner's model correction, a separate first-turn probe used `gpt-6.1-sol` / `low` (provider `headroom`) on thread `01a117f7-148b-7332-b251-fb27482cd6ef`, turn `01a117f7-151f-7213-8887-a42dfed49ca6`; it returned `CAS_FIRST_TURN_OK`. This was the default model, not the user's selected family. No further inference used Sol. Cleanup request `caspr10-20261007-cleanup` was accepted; exact-ID readback failed closed as expected. That thread is deleted.

No disposable threads or queue items are pending from these runs. No unrelated thread was read or changed.

## Tests and hosted checks

Final local checks ran after restoring source/tests to the exact tested commit:

- `.venv/bin/pytest -q -rs`: **513 passed, 15 skipped** in 32.63s.
- Skipped: 5 `tests/test_container_image.py:67` cases because `CODEX_ACTION_CONTAINER_IMAGE` was unset; 10 `tests/test_package_composition.py` cases (5 at line 576, 1 at line 613, 4 at line 640) because `CAS_COMPOSITION_RUNTIME=1` was unset.
- `.venv/bin/ruff check src tests scripts/preflight.py`: passed.
- `.venv/bin/ruff format --check src tests scripts/preflight.py`: passed; 66 files already formatted.
- `ghx pr checks 10 --repo joshyorko/codex-action-server`: `test` (2 jobs), `verify`, and `provider (docker)` / `provider (podman)` passed; `publish` skipped.
- A test-first parser experiment was not retained: its focused run exposed five existing spoof/ambiguity regressions, so both the experiment and its temporary test were reverted. No product source or test file differs from the exact tested commit.

## Exact source/test identities and clean-environment limits

- Commit: `92b569bcaa953c2914a90bbd1ca42c2880832e8a`.
- `src/codex_rpc.py` SHA-256: `2a6e8957081da9c32e1f69debbe5074b4b4f488efc552c415a7204c519d1cda6`.
- `tests/test_native_capabilities.py` SHA-256: `ee1d1b5f1bf4e6b295c7af62a767abc8cda5dfee05a9d32dc2a62268dcfd03db`.
- Host Python was 3.14.8. System `pytest` was absent; repo-declared dependencies were installed with `uv` into ignored `.venv/`. This is a local environment, not a clean CI/container image proof. The container-image and serialized package-composition checks remain skipped locally.
- Acceptance command: execute the contents of `scripts/acceptance/cas_pr10_dakota.codemode.js` through Executor Codemode. Recorded run used Executor tool calls `codex.create_thread_and_start_turn`, `set_thread_name`, `add_thread_queue_item`, `start_turn`, `steer_turn`, `list_thread_turns`, `interrupt_turn`, `get_thread_snapshot`, `delete_thread`, and exact-ID `read_thread`; outer Executor result was completed/ok. Raw receipts and secret-bearing environment were not copied into this file.

## Readiness decision

**`DEPLOY_READY=false`.** PR #10's manifest and revision are running, local tests and hosted checks passed, and the safe stable control path was exercised. Production readiness is not established because the required experimental queue/diagnostics gates reject the live `0.161.0` daemon against CAS's pinned `0.160.1` contract, clean image/composition acceptance was skipped, and Docker's local config view contains the noted Healthcheck metadata difference. No fix is being invented from the version difference alone. Next evidence must come from an exact supported native schema or a separately reviewed `0.161.0` schema/UA compatibility update, plus reconciliation of the config metadata view, followed by the skipped image/composition acceptance and queue/diagnostics live gates. Do not redeploy as part of this receipt review.
