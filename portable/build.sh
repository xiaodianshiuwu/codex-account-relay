#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
"$PYTHON" "$ROOT/tools/build_portable.py"
echo "产物: $ROOT/dist/CodexRelay"
