#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

PYTHON_BIN="${PYTHON:-python}"
exec "$PYTHON_BIN" -m mobguard_module.dev_local stop "$@"
