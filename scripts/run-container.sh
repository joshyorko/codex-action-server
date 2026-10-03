#!/usr/bin/env bash
# Container-only launcher. Host startup retains scripts/run.sh and loopback.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
umask 077
python3 - <<'PY'
import ipaddress
import os
from pathlib import Path
import sys

try:
    address = ipaddress.IPv4Address(os.environ["CODEX_ACTION_BRIDGE_GATEWAY"])
    networks = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
    if not any(address in ipaddress.IPv4Network(network) for network in networks):
        raise ValueError()
    if os.environ.get("CODEX_ACTION_PORT", "8088") != "8088":
        raise ValueError()
    home = Path(os.environ["ACTIONS_HOME"])
    if not home.is_absolute() or home.is_symlink():
        raise ValueError()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    stat = home.stat()
    if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
        raise ValueError()
except (KeyError, OSError, ValueError):
    print("Container startup requires an explicit RFC1918 IPv4 bridge gateway and private owned Actions Runtime state", file=sys.stderr)
    sys.exit(2)
PY
port="$(python3 "$root/scripts/preflight.py")"
# Baked environments stay outside the mounted state. Add only missing cache
# entries, so an existing private state directory never hides the image cache.
if [ -n "${CODEX_ACTION_PREPARED_CACHE:-}" ]; then
    test -d "$CODEX_ACTION_PREPARED_CACHE"
    shopt -s dotglob nullglob
    cache_entries=("$CODEX_ACTION_PREPARED_CACHE"/*)
    if (( ${#cache_entries[@]} )); then
        cp -a --no-clobber "${cache_entries[@]}" "$ACTIONS_HOME/"
    fi
fi
exec "${ACTION_SERVER_BIN:-action-server}" start --dir "$root" --datadir "$CODEX_ACTION_DATA" --address "$CODEX_ACTION_BRIDGE_GATEWAY" --port "$port" --actions-sync=true --min-processes 1 --max-processes 1
