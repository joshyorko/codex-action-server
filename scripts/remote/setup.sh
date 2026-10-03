#!/usr/bin/env bash
set -euo pipefail

BREW="${CODEX_WORKER_BREW:-/home/linuxbrew/.linuxbrew/bin/brew}"
CODEX_HOME="${CODEX_WORKER_CODEX_HOME:-${CODEX_HOME:-/home/vscode/.codex}}"
CODEX="${CODEX_WORKER_CODEX_BIN:-/home/vscode/.local/bin/codex}"
GH="${CODEX_WORKER_GH_BIN:-/home/linuxbrew/.linuxbrew/bin/gh}"
PYTHON="${CODEX_WORKER_PYTHON:-/home/linuxbrew/.linuxbrew/bin/python3}"
HEADROOM="${CODEX_WORKER_HEADROOM_BIN:-/home/linuxbrew/.linuxbrew/bin/headroom}"
RTK="${CODEX_WORKER_RTK_BIN:-/home/linuxbrew/.linuxbrew/bin/rtk}"
BREW_CURL="${CODEX_WORKER_CURL_BIN:-/usr/bin/curl}"
CONFIG="${CODEX_WORKER_CODEX_CONFIG:-$CODEX_HOME/config.toml}"
HEADROOM_URL="${CODEX_WORKER_HEADROOM_URL:-http://10.10.10.89/v1}"
endpoint_pattern='^https?://[A-Za-z0-9._:/-]+$'
if [[ ! "$HEADROOM_URL" =~ $endpoint_pattern ]]; then
  printf 'Invalid credential-free HTTP(S) Headroom endpoint.\n' >&2
  exit 2
fi
if [[ "$CODEX" != */* ]]; then
  printf 'CODEX_WORKER_CODEX_BIN must name an executable path.\n' >&2
  exit 2
fi
if [[ "$CONFIG" != "$CODEX_HOME/config.toml" ]]; then
  printf 'CODEX_WORKER_CODEX_CONFIG must be inside the selected CODEX_HOME.\n' >&2
  exit 2
fi
CODEX_INSTALL_DIR="${CODEX%/*}"
export CODEX_HOME

test -x "$BREW"

config_created=0
if ! test -x "$GH"; then
  "$BREW" install gh
fi
if ! test -x "$PYTHON"; then
  "$BREW" install python
fi
if ! test -x "$RTK"; then
  "$BREW" install rtk
fi

mkdir -p "${CONFIG%/*}"
if ! test -f "$CONFIG"; then
  config_created=1
  /usr/bin/printf '%s\n' \
    'model = "gpt-6-luna"' \
    'model_reasoning_effort = "max"' \
    'model_context_window = 1000000' \
    'model_auto_compact_token_limit = 900000' \
    '' \
    '[model_providers.headroom]' \
    "base_url = \"$HEADROOM_URL\"" \
    'requires_openai_auth = true' > "$CONFIG"
fi

"$BREW" tap joshyorko/tools
if ! test -x "$HEADROOM"; then
  "$BREW" install joshyorko/tools/headroom-self-hosted
fi

if ! test -x "$CODEX"; then
  installer=$(mktemp)
  trap 'rm -f "$installer"' EXIT
  "$BREW_CURL" -fsSL https://chatgpt.com/codex/install.sh -o "$installer"
  CODEX_NON_INTERACTIVE=1 \
    CODEX_INSTALL_DIR="$CODEX_INSTALL_DIR" \
    CODEX_HOME="$CODEX_HOME" \
    /usr/bin/sh "$installer"
fi

if (( config_created == 0 )); then
  printf 'Keeping the existing Codex configuration.\n'
  printf 'Select Headroom explicitly with the configured endpoint when desired.\n'
else
  "$HEADROOM" init --global --proxy-url "$HEADROOM_URL" codex
  # Headroom infers authentication from the current login. A fresh worker has
  # none yet, but must retain the recipe's policy for a later interactive login.
  "$PYTHON" - "$CONFIG" <<'PY'
from pathlib import Path
import re
import sys
import tomllib

path = Path(sys.argv[1])
content = path.read_text()
provider = tomllib.loads(content)["model_providers"]["headroom"]
if provider.get("requires_openai_auth") is not True:
    table = re.search(
        r"(?ms)^([ \t]*\[model_providers\.headroom\][ \t]*(?:#[^\n]*)?\n)"
        r"(.*?)(?=^[ \t]*\[|\Z)",
        content,
    )
    if table is None:
        raise SystemExit("Headroom did not emit its expected provider table")
    body = re.sub(
        r"(?m)^[ \t]*requires_openai_auth[ \t]*=[^\n]*(?:\n|\Z)",
        "",
        table[2],
    )
    replacement = table[1] + "requires_openai_auth = true\n" + body
    updated = content[:table.start()] + replacement + content[table.end():]
    tomllib.loads(updated)
    path.write_text(updated)
PY
fi
"$RTK" init --codex
"$RTK" verify
"$CODEX" plugin marketplace add joshyorko/plugins --ref main
"$CODEX" plugin add luna-factory@plugins
