#!/usr/bin/env bash
# Host composition launcher; package selection is CODEX_ACTION_PACKAGES.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$root/scripts/run.sh"
