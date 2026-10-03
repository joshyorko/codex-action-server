# Tonight: standalone cutover, existing Codex untouched

The three PRs are review candidates, not merged. Use the exact SHAs in the handoff
as STANDALONE_SHA, FRIDAY_SHA, and TUNNEL_KIT_SHA. Run on Dakota as kdlocpanda.
Do not run these on the Work VM. This first slice deliberately does not restart
native Codex, Devsy, Review or Josh Room. Tunnel reconnect is expected.

## 1. Acquire isolated candidate checkouts

```bash
: "${STANDALONE_SHA:?Copy exact standalone SHA from handoff}"
: "${FRIDAY_SHA:?Copy exact Friday SHA from handoff}"
: "${TUNNEL_KIT_SHA:?Copy exact tunnel-kit SHA from handoff}"
CUTOVER="$HOME/second_brain/Resources/Sandbox/codex-action-server-cutover-20261002"
mkdir -p "$CUTOVER"
cd "$CUTOVER"
git clone https://github.com/joshyorko/codex-action-server.git server
git -C server fetch origin feat/standalone-control-plane
git -C server checkout --detach "$STANDALONE_SHA"
git clone https://github.com/joshyorko/friday.git friday-consumer
git -C friday-consumer fetch origin feat/standalone-codex-consumer
git -C friday-consumer checkout --detach "$FRIDAY_SHA"
git clone https://github.com/joshyorko/mcp-tunnel-kit.git tunnel-kit
git -C tunnel-kit fetch origin feat/standalone-codex-target-probes
git -C tunnel-kit checkout --detach "$TUNNEL_KIT_SHA"
uv venv tunnel-kit/.venv
uv pip install --python tunnel-kit/.venv/bin/python -r tunnel-kit/requirements.txt
command -v action-server
```

If a checkout directory already exists, stop and inspect it; do not overwrite an
active worktree. Actions Runtime1.0.1 is already installed on Dakota. Reuse it.

## 2. Configure Dakota explicitly, remote separately

Start with ONLY the known Dakota socket; remote discovery must not block this.

```bash
mkdir -p "$HOME/.config/codex-action-server"
export CODEX_ACTION_TARGETS="$HOME/.config/codex-action-server/targets.json"
export CODEX_ACTION_RECEIPTS="$HOME/.local/state/codex-action-server/receipts"
export CODEX_ACTION_DATA="$HOME/.local/state/codex-action-server/runtime"
export CODEX_ACTION_PORT=8088
# Refuse to overwrite an existing operator configuration.
test ! -e "$CODEX_ACTION_TARGETS" || { echo 'Existing target config: inspect before changing'; exit 1; }
python3 - <<'PY'
import json, os
from pathlib import Path
p=Path(os.environ['CODEX_ACTION_TARGETS'])
with p.open('x') as f:
    json.dump({'targets': {'local': {'transport': 'local', 'codex_bin': 'codex',
        'socket_path': '/home/kdlocpanda/.codex/app-server-control/app-server-control.sock'}}}, f, indent=2)
PY
cd "$CUTOVER/server"
./scripts/run.sh
```

Keep this foreground terminal open. It starts a NEW control API on8088. The
existing native Codex socket/daemon is only connected to, never replaced. The
old Friday8087API may remain running for old consumers. Do not pull this Friday
migration into the active old service checkout before cutover.

## 3. Read-only local probe, then reconnect the tunnel

In another Dakota terminal, reuse the existing control-plane environment. Never
print the key. Set CUTOVER again because shells do not share variables.

```bash
CUTOVER="$HOME/second_brain/Resources/Sandbox/codex-action-server-cutover-20261002"
export CODEX_MCP_URL=http://127.0.0.1:8088/mcp
"$CUTOVER/tunnel-kit/.venv/bin/python" "$CUTOVER/tunnel-kit/codex_mcp_check.py" --probe --target local --cwd /home/kdlocpanda/second_brain/Projects/automation-control-plane/RPA/robots/JATT/josh-room --require-thread
```

Only after that passes, Ctrl-C the OLD foreground tunnel-client, then run:

```bash
: "${CONTROL_PLANE_API_KEY:?Export the existing runtime key in this shell}"
: "${CONTROL_PLANE_TUNNEL_ID:?Export the existing tunnel ID in this shell}"
"$HOME/.local/bin/tunnel-client" run \
  --control-plane.api-key env:CONTROL_PLANE_API_KEY \
  --control-plane.tunnel-id "$CONTROL_PLANE_TUNNEL_ID" \
  --health.listen-addr 127.0.0.1:0 \
  --mcp.server-url 'url=http://127.0.0.1:8088/mcp,channel=main'
```

Equivalent kit command: CODEX_MCP_URL=http://127.0.0.1:8088/mcp
`"$CUTOVER/tunnel-kit/launch-codex.zsh"`. Run only one client for this tunnel ID.
Reconnect/refresh the ChatGPT app to replace the old tool catalog. Expect23tools,
including list_targets, inspect_target and read_dispatch_receipt. Old enum-only
remote target schemas mean the catalog is stale.

## 4. First five read-only ChatGPT MCP calls, in order

These exact IDs/cwds were read from existing native local metadata during the
investigation. If an ID is absent or cwd differs, stop and reconcile discovery;
never create a replacement.

1. read_server_diagnostics
   `{"payload":{"target":"local"}}`
2. read_model_provider_capabilities
   `{"payload":{"target":"local"}}`
3. discover_threads
   `{"payload":{"target":"local","limit":100}}`
4. read_thread (Review coordinator)
   `{"payload":{"target":"local","cwd":"/home/kdlocpanda/second_brain/Areas/dinosaurs/projectbluefin/review","thread_id":"01a0fe92-10f7-7620-8b87-b565bff05650","include_turns":false}}`
5. read_thread (Josh Room coordinator)
   `{"payload":{"target":"local","cwd":"/home/kdlocpanda/second_brain/Projects/automation-control-plane/RPA/robots/JATT/josh-room","thread_id":"01a0fe86-7558-7e21-a060-54e2139ceac3","include_turns":false}}`

Then list_models with `{"payload":{"target":"local"}}` for the actual model and
reasoning catalog. Do not infer provider inheritance from a model name.

## 5. Friday becomes a consumer

After standalone local read-only proof, in the operator shell:

```bash
CUTOVER="$HOME/second_brain/Resources/Sandbox/codex-action-server-cutover-20261002"
bash "$CUTOVER/friday-consumer/scripts/connect-codex-action-server.sh" http://127.0.0.1:8088/mcp --apply
```

This uses `friday config set`, changing only the named MCP URL/enabled fields.
Start a new Friday turn to refresh its catalog when safe. No shared gateway restart.
For legacy direct RPC/TTS scripts, set CODEX_ACTION_SERVER_ROOT to the pinned server
checkout in that consumer's environment, or install the standalone Python package.
Friday no longer distributes an Action Server implementation; its RPC entrypoint
is a thin loader and its former installer only explains migration.

## 6. Only after Dakota passes: configure/prove Devsy read-only

Devsy desktop/workspace connection must already be running. Choose a unique
workspace from authoritative JSON, not the most recently used one. This command
selects exactly one Kubernetes workspace sourced from Friday, matching tonight's
recorded topology. It fails rather than guessing when zero/multiple candidates exist.

```bash
export CODEX_ACTION_TARGETS="$HOME/.config/codex-action-server/targets.json"
devsy --context default --result-format json workspace list --skip-pro > /tmp/codex-devsy-workspaces.json
python3 - <<'PY'
import json, os
from pathlib import Path
rows=json.loads(Path('/tmp/codex-devsy-workspaces.json').read_text())
matches=[r for r in rows if r.get('context')=='default' and r.get('provider',{}).get('name')=='kubernetes' and r.get('source',{}).get('gitRepository')=='https://github.com/joshyorko/friday.git']
if len(matches)!=1: raise SystemExit('Expected one matching workspace; inspect Devsy selection, do not guess')
r=matches[0]
p=Path(os.environ['CODEX_ACTION_TARGETS']); data=json.loads(p.read_text())
data['targets']['devsy']={'transport':'devsy','context':'default','provider':'kubernetes','workspace':r['id'],'workspace_uid':r['uid'],'user':'vscode','codex_bin':'/home/vscode/.local/bin/codex'}
tmp=p.with_suffix('.pending'); tmp.write_text(json.dumps(data,indent=2)+'\n'); tmp.replace(p)
print('Configured logical devsy from authoritative workspace identity:',r['id'])
PY
```

Target configuration is read per request: no server/tunnel/native restart needed.
From ChatGPT call list_targets, inspect_target(devsy), read_server_diagnostics(devsy),
discover_threads(devsy). Use the returned exact cwd and ID in read_thread(devsy).
No empty list may be called a read proof. Resolver checks list/status, authoritative
SSH config, pinned TCP route/user, and verifies remote DEVSY_WORKSPACE_UID/ID before
each native probe/proxy command. Missing environment identity fails closed.

Do NOT create a test thread until BOTH Dakota and Devsy read-only proofs pass.
This handoff stops at read-only acceptance; mutation validation is a later explicit
operator step. Retain request_id before any future dispatch and read its receipt
on uncertainty. No blind resubmission with a new ID.

## Restart summary and rollback

- Start new standalone control API; restart/reconnect only tunnel exposure.
- Update Friday's MCP URL and refresh its catalog in a new turn.
- Do not restart native Codex, Devsy, Review or Josh Room.
- To rollback, stop only the new tunnel-client and re-run the same command with
  upstream `http://127.0.0.1:8087/mcp`. Restore Friday's old URL if changed.
- Preserve dispatch receipts across any API restart. The old service/checkouts
  remain operator rollback material until you choose to retire them.

Native Unix-socket and hosted remote proofs are operator acceptance on Dakota.
They were not performed by changing your live system from this VM.
