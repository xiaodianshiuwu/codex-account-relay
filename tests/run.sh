#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"
export CODEX_RELAY_TEST_MODE=1
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" exec "${PYTHON:-/usr/bin/python3}" "$ROOT/tests/run.py"
