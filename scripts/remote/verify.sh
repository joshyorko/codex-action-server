#!/usr/bin/env bash
set -euo pipefail

GH="${CODEX_WORKER_GH_BIN:-/home/linuxbrew/.linuxbrew/bin/gh}"
PYTHON="${CODEX_WORKER_PYTHON:-/home/linuxbrew/.linuxbrew/bin/python3}"
HEADROOM="${CODEX_WORKER_HEADROOM_BIN:-/home/linuxbrew/.linuxbrew/bin/headroom}"
RTK="${CODEX_WORKER_RTK_BIN:-/home/linuxbrew/.linuxbrew/bin/rtk}"
CODEX_HOME="${CODEX_WORKER_CODEX_HOME:-${CODEX_HOME:-/home/vscode/.codex}}"
CODEX="${CODEX_WORKER_CODEX_BIN:-/home/vscode/.local/bin/codex}"
CONFIG="${CODEX_WORKER_CODEX_CONFIG:-$CODEX_HOME/config.toml}"
if [[ "$CODEX" != */* ]]; then
  printf 'CODEX_WORKER_CODEX_BIN must name an executable path.\n' >&2
  exit 2
fi
if [[ "$CONFIG" != "$CODEX_HOME/config.toml" ]]; then
  printf 'Codex config path must be CODEX_HOME/config.toml.\n' >&2
  exit 2
fi
export CODEX_HOME

printf 'If this Codex home is not authenticated, open an interactive terminal and run: %s login\n' "$CODEX"

for executable in "$GH" "$PYTHON" "$HEADROOM" "$RTK" "$CODEX"; do
  test -x "$executable"
done

if ! test -f "$CONFIG"; then
  printf 'Codex configuration file is missing.\n' >&2
  exit 1
fi
if ! "$PYTHON" - "$CONFIG" >/dev/null 2>&1 <<'PY'
import sys
import tomllib

with open(sys.argv[1], "rb") as config_file:
    config = tomllib.load(config_file)
providers = config.get("model_providers", {})
if config.get("model_provider") == "headroom":
    headroom = providers.get("headroom")
    if not isinstance(headroom, dict) or not headroom.get("base_url"):
        raise SystemExit("Codex config has no Headroom provider endpoint")
    if headroom.get("requires_openai_auth") is not True:
        raise SystemExit("Headroom provider must require OpenAI authentication")
for key in ("model", "model_provider", "model_reasoning_effort"):
    value = config.get(key)
    if value is not None and (not isinstance(value, str) or not value):
        raise SystemExit(f"Codex config has an invalid {key}")
PY
then
  printf 'Codex configuration validation failed.\n' >&2
  exit 1
fi

if ! "$CODEX" --version >/dev/null 2>&1; then
  printf 'Codex CLI check failed.\n' >&2
  exit 1
fi
if ! daemon="$("$CODEX" app-server daemon version 2>/dev/null)"; then
  printf 'Codex App Server daemon is not ready.\n' >&2
  exit 1
fi
if ! printf '%s' "$daemon" | "$PYTHON" -c \
  'import json,sys; data=json.load(sys.stdin); raise SystemExit(0 if data.get("status") == "running" and data.get("socketPath") else 1)' >/dev/null 2>&1; then
  printf 'Codex App Server daemon readiness check failed. Start it with the configured Codex executable.\n' >&2
  exit 1
fi
printf 'Codex App Server daemon ready.\n'
if ! "$HEADROOM" --version >/dev/null 2>&1; then
  printf 'Headroom CLI check failed.\n' >&2
  exit 1
fi
if ! "$RTK" --version >/dev/null 2>&1 || ! "$RTK" verify >/dev/null 2>&1; then
  printf 'RTK verification failed.\n' >&2
  exit 1
fi
if ! plugin_json="$("$CODEX" plugin list --marketplace plugins --json 2>/dev/null)"; then
  printf 'Codex plugin catalog check failed.\n' >&2
  exit 1
fi
if ! printf '%s' "$plugin_json" | "$PYTHON" -c \
  'import json,sys; plugins=json.load(sys.stdin).get("installed", []); raise SystemExit(0 if any(item.get("pluginId") == "luna-factory@plugins" and item.get("installed") is True and item.get("enabled") is True for item in plugins) else 1)' >/dev/null 2>&1; then
  printf 'Codex worker plugin readiness check failed.\n' >&2
  exit 1
fi

