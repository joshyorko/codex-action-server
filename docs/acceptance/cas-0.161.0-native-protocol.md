# CAS Codex 0.161.0 native protocol compatibility

## Scope and result

Compared the generated native app-server schemas from Codex 0.160.1 and
0.161.0, then exercised the reviewed diagnostics and queue methods through this
worktree's typed CAS entrypoints against the already-running local native
daemon. The process-local daemon and its socket were not restarted. The
acceptance used a one-target temporary config, temporary CAS receipts, and one
new disposable thread that was deleted at the end.

The reviewed 0.161.0 gate admits `server/diagnostics` and the six
`thread/queue/*` operations. The other exposed experimental methods remain
pinned to 0.160.1. Native methods outside CAS's typed surface remain rejected.

## Source and generated schema identity

| Native version | Codex source commit | Default requests | With experimental | Generated schema SHA-256 |
| --- | --- | ---: | ---: | --- |
| 0.160.1 | `d27764b82f7118f674371e6d6e76271d9d606edb` | 104 | 167 | Root `7243ba241962af92ca60581f1a81808ebda4212a800f8b205f54703bcfd508c5`; v2 `e77b7d1436a78f431a74b2cb263a862e92ae40d70411bc63835b47ab2168827c` |
| 0.161.0 | `979011409de0a60b52f179721948e65531d26144` | 104 | 169 | Root `e7eb93e544b11833bd4ac39d14ec6ef26791b5b1ea43db0e451d772d6d27067a`; v2 `dc1945f0a8c4ebbe063d2c1533aaa7f9b152fb810ab60e7b389dbac9f9c9c2fa` |

The two additional experimental-only requests in 0.161.0 are
`account/bedrock/checkGovCloudRequirements` and `thread/prediction/request`;
CAS does not expose either one.

The comparison is reproducible with
[`compare_native_protocol_schemas.py`](../../scripts/compare_native_protocol_schemas.py).
Its per-method request and response fingerprints are in
[`codex_0.160.1_to_0.161.0_experimental_compatibility.json`](../../tests/fixtures/protocol/codex_0.160.1_to_0.161.0_experimental_compatibility.json).

## Exposed experimental contract matrix

Every request contract is unchanged. Twelve response contracts are unchanged.
The other three widen the nested `CodexErrorInfo` response shape from known
`oneOf` variants to `anyOf` plus an unknown string/object case. CAS forwards
these native response objects as bounded JSON. The queue-start mapping still
requires the exact returned turn ID. Search and timeline remain gated because
they are outside this compatibility slice.

| Native method | Typed request | Typed response | 0.160.1 → 0.161.0 |
| --- | --- | --- | --- |
| `app/read` | `AppsReadParams` | `AppsReadResponse` | Request and response unchanged |
| `server/diagnostics` | `ServerDiagnosticsParams` | `ServerDiagnosticsResponse` | Request and response unchanged |
| `thread/backgroundTerminals/list` | `ThreadBackgroundTerminalsListParams` | `ThreadBackgroundTerminalsListResponse` | Request and response unchanged |
| `thread/backgroundTerminals/terminate` | `ThreadBackgroundTerminalsTerminateParams` | `ThreadBackgroundTerminalsTerminateResponse` | Request and response unchanged |
| `thread/queue/add` | `ThreadQueueAddParams` | `ThreadQueueAddResponse` | Request and response unchanged |
| `thread/queue/delete` | `ThreadQueueDeleteParams` | `ThreadQueueDeleteResponse` | Request and response unchanged |
| `thread/queue/list` | `ThreadQueueListParams` | `ThreadQueueListResponse` | Request and response unchanged |
| `thread/queue/reorder` | `ThreadQueueReorderParams` | `ThreadQueueReorderResponse` | Request and response unchanged |
| `thread/queue/start` | `ThreadQueueStartParams` | `ThreadQueueStartResponse` | Request unchanged; nested turn error response widened |
| `thread/queue/update` | `ThreadQueueUpdateParams` | `ThreadQueueUpdateResponse` | Request and response unchanged |
| `thread/search` | `ThreadSearchParams` | `ThreadSearchResponse` | Request unchanged; nested turn error response widened |
| `thread/searchOccurrences` | `ThreadSearchOccurrencesParams` | `ThreadSearchOccurrencesResponse` | Request and response unchanged |
| `thread/settings/update` | `ThreadSettingsUpdateParams` | `ThreadSettingsUpdateResponse` | Request and response unchanged |
| `thread/timeline/list` | `ThreadTimelineListParams` | `ThreadTimelineListResponse` | Request unchanged; turn-completed error response widened |
| `turn/settings/update` | `TurnSettingsUpdateParams` | `TurnSettingsUpdateResponse` | Request and response unchanged |

`app/read` is a default native request but remains in CAS's experimental gate
set. Fourteen of the fifteen CAS-gated methods are experimental-only in the
native schema.

## Native daemon behavior and live acceptance

The earlier 0.161.0 diagnostics failure was the local 0.160.1 version gate:
the method was never sent. With the reviewed gate, CAS sent
`server/diagnostics`; the native daemon replied successfully with process
identity and six gauges. The typed result is content-free and process-local.

The queue test held a disposable turn active while it queued two items. Native
list returned both exact IDs; update changed the first item's text; reorder
returned the requested order; both deletes were accepted. Starting a queued
item while the thread was active returned the native state error. After
interrupting that exact turn, the harness queued another item and
`thread/queue/start` accepted it; its turn completed and the final queue was
empty. The exact thread was deleted and its readback failed closed.

The 0.161.0 source confirms two relevant state limits: queue start requires a
loaded idle thread, and a newly queued item on a loaded idle thread can be
started automatically. Ephemeral threads reject queued submissions. If the
daemon has no queue service installed, queue operations return an invalid
request. These are native runtime limits; matching schemas alone do not imply
that every call is valid for every thread state.

The live run was `cas01610-2582e5c83e0d` on disposable thread
`01a11815-0c24-7a70-b288-0a6d7118cede`. Its bounded output and private receipt
store are retained under `/tmp/cas-01610-acceptance/run.zv248dg4/` on Dakota.
The harness is
[`cas_01610_live_acceptance.py`](../../scripts/acceptance/cas_01610_live_acceptance.py).

The previous PR #10 receipt and its Executor Codemode harness remain unchanged
at `docs/acceptance/cas-pr10-dakota-receipt.md` and
`scripts/acceptance/cas_pr10_dakota.codemode.js`.

## Verification status

Protocol fixtures, full tests, Ruff, image acceptance, and package-composition
acceptance are recorded in the final receipt and machine summary alongside
this document. `DEPLOY_READY` is set only after those gates finish.
