#!/usr/bin/env bash
set -euo pipefail
set -eu; CODEX_HOME="${CODEX_WORKER_CODEX_HOME:-${CODEX_HOME:-/home/vscode/.codex}}"; CODEX_BIN="${CODEX_WORKER_CODEX_BIN:-/home/vscode/.local/bin/codex}"; CODEX_CONFIG="${CODEX_WORKER_CODEX_CONFIG:-$CODEX_HOME/config.toml}"; if [ "$CODEX_CONFIG" != "$CODEX_HOME/config.toml" ]; then printf 'Codex config path must be CODEX_HOME/config.toml\n' >&2; exit 2; fi; export CODEX_HOME CODEX_WORKER_CODEX_HOME="$CODEX_HOME" CODEX_WORKER_CODEX_BIN="$CODEX_BIN"; "$CODEX_BIN" app-server daemon start; /bin/bash /opt/codex-worker/verify.sh
