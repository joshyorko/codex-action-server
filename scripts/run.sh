#!/usr/bin/env bash
# Starts ONLY this control API; never starts or restarts a Codex/Devsy daemon.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${CODEX_ACTION_TARGETS:?Set the absolute operator-owned target JSON path}"
: "${CODEX_ACTION_RECEIPTS:?Set a persistent private receipt directory}"
: "${CODEX_ACTION_DATA:?Set a separate Action Server data directory}"
umask 077
port="$(python3 "$root/scripts/preflight.py")"
exec "${ACTION_SERVER_BIN:-action-server}" start --dir "$root" --datadir "$CODEX_ACTION_DATA" --address 127.0.0.1 --port "$port" --actions-sync=true --min-processes 1 --max-processes 1
