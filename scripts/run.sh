#!/usr/bin/env bash
# Starts ONLY this control API; never starts or restarts a Codex/Devsy daemon.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${CODEX_ACTION_TARGETS:?Set the absolute operator-owned target JSON path}"
: "${CODEX_ACTION_RECEIPTS:?Set a persistent private receipt directory}"
: "${CODEX_ACTION_DATA:?Set a separate Action Server data directory}"
port="${CODEX_ACTION_PORT:-8088}"
[[ "$port" =~ ^[0-9]+$ ]] && (( port > 1023 && port <= 65535 )) || { echo 'Invalid unprivileged port' >&2; exit 2; }
[[ "$CODEX_ACTION_TARGETS" = /* && -f "$CODEX_ACTION_TARGETS" && "$CODEX_ACTION_DATA" = /* && "$CODEX_ACTION_RECEIPTS" = /* ]] || { echo 'Absolute configuration/state paths required' >&2; exit 2; }
exec "${ACTION_SERVER_BIN:-action-server}" start --dir "$root" --datadir "$CODEX_ACTION_DATA" --address 127.0.0.1 --port "$port" --actions-sync=true --min-processes 1 --max-processes 1
