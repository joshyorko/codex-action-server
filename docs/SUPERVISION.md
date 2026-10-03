# Supervise the proven control path

These are **candidate operator steps, requiring Josh's deployment approval**.
Preparing templates does not install, enable, start, stop, or restart a service.
Keep the old Friday API on 8087 and its old tunnel available as rollback.

## What the live inspection established

The first 8088 control API and its blue tunnel worked, but were detached processes
in a terminal scope. Neither had a dedicated service. The old API belonged to
`friday-codex-action-server.service`. Both APIs reached the same existing native
Codex daemon. PIDs are observations, not safe future kill targets.

Use two separately owned user units:

- This repository owns `deploy/codex-action-server.service`
- mcp-tunnel-kit owns `deploy/codex-mcp-blue-tunnel.service`

The server remains loopback-only. The tunnel requires the new control API unit
and stops/restarts with that unit. Type=simple ordering is not readiness: a running
unit does not prove HTTP/MCP or native connectivity; repeat the read-only probe
after every startup. Neither unit references or manages the native
Codex daemon, Devsy, Friday gateway, or old API/tunnel. The units do not restart
native Codex when connectivity is unavailable. They retry their **own process**
at most three starts per 120 seconds, with ten seconds between failures.

`KillMode=control-group` cleans up the unit's wrapper and connection proxies. It
must never own the native Codex daemon: that process must already be running
outside these units, as in the proven topology.

## Prepare without activating

1. Check out the exact reviewed server and tunnel-kit commits into separate
   immutable release directories. Do not edit the currently running checkout.
2. Inspect `deploy/service.env.example` and the kit's
   `deploy/codex-blue.env.example`. Replace placeholder paths with literal
   absolute paths to those checkouts and the verified executables. Systemd
   environment files are **not shell scripts**: no `export`, `$HOME`, or sourcing
   `.zshrc`. Preserve the existing target JSON, state, and receipt paths. Never
   put state inside a release directory that an upgrade will remove.
3. The operator supplies the existing blue tunnel credentials locally. Use its
   blue tunnel ID, not the still-running old tunnel's ID. Do not print, copy into
   chat, commit, or generate credentials. Keep the actual environment files
   owner-only. The server environment file needs no tunnel key.
4. Stage the two unit files and environment files for review. Do not overwrite
   an existing file or change its permissions without inspecting it first.
5. Validate the staged units without activation:

   ```bash
   systemd-analyze --user verify /absolute/staging/codex-action-server.service \
     /absolute/staging/codex-mcp-blue-tunnel.service
   ```

6. In a shell with the **server's non-secret settings** exported, run the offline
   preflight with the same Python executable available to the service:

   ```bash
   python3 /absolute/server/scripts/preflight.py
   ```

   It validates decimal port range, target JSON shape/transports, and private
   writable state directories. It creates missing state directories with a
   private umask, tests writes/fsync, and fsyncs newly created directory entries. It
   does not contact native Codex or Devsy. Existing non-private directories fail
   rather than being silently chmodded. Successful output is the normalized port.
   This is startup validation, not live MCP or native-socket acceptance.

## Activate only after explicit approval

Use the user manager as the Dakota desktop user. If `systemctl --user` cannot
reach the session bus, use the operator's logged-in desktop shell. Do not invent
another supervisor or enable lingering as a workaround.

1. Verify current 8088 and blue-tunnel process identity, command, start time,
   cgroup, and log ownership. Record the actual old and new tunnel identities
   without displaying credentials. Do not reuse a previously reported PID.
2. Put the reviewed files at:
   - `~/.config/systemd/user/codex-action-server.service`
   - `~/.config/systemd/user/codex-mcp-blue-tunnel.service`
   - `~/.config/codex-action-server/service.env`
   - `~/.config/mcp-tunnel-kit/codex-blue.env`
3. Stop only the exact verified **candidate** blue tunnel and ad hoc 8088 API,
   using their established process owner. Confirm port 8088 is free. Do not use
   `pkill codex`, `pkill action-server`, or kill the old 8087 service/tunnel.
4. Load the units and start only the new API:

   ```bash
   systemctl --user daemon-reload
   systemctl --user start codex-action-server.service
   systemctl --user status codex-action-server.service --no-pager
   ```

5. Run the kit's read-only proof against `http://127.0.0.1:8088/mcp`, using the
   existing Josh Room cwd and `--target local --require-thread`. If it fails,
   stop here. Do not start a test thread or use the tunnel as a workaround.
6. Start the reviewed blue tunnel:

   ```bash
   systemctl --user start codex-mcp-blue-tunnel.service
   systemctl --user status codex-mcp-blue-tunnel.service --no-pager
   ```

7. Reconnect the **blue** ChatGPT app if necessary. Repeat the five read-only
   calls in OPERATIONS.md, then Devsy resolution, diagnostics, and an existing
   thread read. All three project roots stay parked. A running unit is not proof
   of connectivity. Neither localhost health nor a catalog proves native access.
8. After successful proof and approval for automatic user-session startup:

   ```bash
   systemctl --user enable codex-action-server.service codex-mcp-blue-tunnel.service
   ```

   This is login/session startup, **not a guarantee of unattended boot**. User
   lingering is a separate operator choice and is not configured here.

## Logs, restart, and rollback

Logs go to the user journal, whose retention policy is managed by the host. Do
not truncate active ad hoc logs or delete receipts during this migration. Do not
paste raw logs without checking them for sensitive request data.

```bash
journalctl --user -u codex-action-server.service -u codex-mcp-blue-tunnel.service -n 80 --no-pager
```

After deployment approval, restarting `codex-action-server.service` also restarts
its dependent blue tunnel. Restart only the tunnel unit for an exposure-only
change. Neither operation restarts the native daemon or resumes paused roots.
Preserve the receipt directory across every restart/upgrade: deleting it destroys
retry deduplication history. A request without `request_id` has no durable replay
protection. An `unknown` receipt requires reconciliation, never automatic retry.

If candidate proof fails, stop only the new tunnel and API units and reconnect
the old ChatGPT app pointing to the retained old tunnel/8087 API. The old path
should already be running; inspect it before attempting to start anything. Do
not run two clients using the same tunnel ID. No merge or rollback retirement is
part of this procedure.

## Tested versus operator proof

Automated tests cover invalid preflight input, private state creation, decimal
port bounds, exception-safe cleanup of only owned proxies, and receipt replay
after a separate process exits without cleanup. Unit syntax is checked with
`systemd-analyze --user verify`. Full live user-manager activation, restart after
login, journal retention, and the exact Dakota credential environment remain
operator proof. This patch has not installed or activated either unit.
