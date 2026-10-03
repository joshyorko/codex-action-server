# Operator cutover (never performed by these PRs)

Keep your currently running coordinators, Codex daemon and Devsy session alive.
Do not run a second tunnel-client with the same tunnel ID.

## Stage alongside production

1. Check out the reviewed standalone PR at its exact reported SHA in a new
   directory. Keep the current Friday checkout and service untouched.
2. Reuse the installed Actions Runtime1.0.1. Create a target JSON outside the repo:
   local transport with codex binary; devsy transport with context `default`, the
   exact current workspace from `devsy --context default --result-format json
   workspace list --skip-pro`, expected user and optional workspace UID.
3. Export CODEX_ACTION_TARGETS (absolute JSON file), CODEX_ACTION_RECEIPTS
   (private persistent directory), CODEX_ACTION_DATA (new runtime directory),
   CODEX_ACTION_PORT=8088. Run `./scripts/run.sh` in a separate terminal.
4. In mcp-tunnel-kit, install its pinned check dependencies. Set
   CODEX_MCP_URL=http://127.0.0.1:8088/mcp and use its read-only probe for local,
   then devsy, with each target's actual cwd. Require nonempty discovery and an
   exact read. Empty list is connectivity evidence only, not a read proof.
5. Require list_targets, inspect_target(devsy), and read_server_diagnostics for
   both targets. Check daemon home and socket identity. Unknown workspace,
   ambiguous selector, missing TCP route, or wrong user is a stop condition.

## Switch only the exposure

After stage acceptance, stop only the old foreground tunnel-client with Ctrl-C.
In the same credential-bearing operator shell run tunnel-kit's launch-codex.zsh
with CODEX_MCP_URL=http://127.0.0.1:8088/mcp. Keep the tunnel ID and runtime key.
No Codex restart is needed. No Devsy restart is needed. The old8087Action Server
may remain for existing consumers while you verify the new8088endpoint.

Refresh/reconnect the ChatGPT app tool catalog if it still has the old enum-only
schema. Confirm new list_targets/inspect_target/read_dispatch_receipt tools and
request_id fields. Call devsy discovery and exact cwd/thread read from ChatGPT.
Do not infer hosted success from the local probe.

Only then, with operator approval, create ONE bounded test using target devsy,
a unique persisted request_id, exact cwd, text asking for one fixed phrase and no
tools, and wait_for_completion:false. Save thread_id and turn_id. Read its exact
turn status until completed/failed/interrupted; do not create another test on an
uncertain response. Retry the SAME key only to recover the dispatch receipt.
If still running when cancellation is appropriate, interrupt that exact test
turn and verify terminal status. Do not interrupt existing coordinators.

## Friday consumption

Use the staged Friday consumer helper to point its MCP profile at the tested URL.
A profile/catalog refresh may be needed on the next Friday turn. Do not restart
the shared Hermes gateway while it owns active conversations solely for catalog
refresh. Switch service management only after the swarms finish and the operator
chooses a quiet window. A user service should run scripts/run.sh with explicit
absolute environment paths and a persistent receipt directory; use a new service
name, not the old Friday unit. Do not tie the standalone API's lifecycle to Hermes.

## Rollback

Stop only the new foreground tunnel-client. Relaunch the old pinned tunnel-kit
with CODEX_MCP_URL=http://127.0.0.1:8087/mcp and the same tunnel ID. Restore Friday's
previous MCP URL only if you changed it. Preserve receipt files, especially any
unknown request outcome. Stop the new control API only after outstanding bounded
calls return. Native Codex threads are not deleted or recreated by rollback.

These steps are an operator validation plan until exact live receipts are recorded.
No production installation, restart, credentials or remote thread mutation was
performed during the isolated implementation.
