#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SHARE="${CODEX_RELAY_SHARE:-$HOME/.local/share/codex-relay}"
BIN_DIR="${CODEX_RELAY_BIN_DIR:-$HOME/.local/bin}"
if [ -n "${CODEX_RELAY_PYTHON:-}" ]; then
  PYTHON="$CODEX_RELAY_PYTHON"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  PYTHON="/usr/bin/python3"
fi

if [ ! -x "$PYTHON" ]; then
  echo "找不到 Python 3：$PYTHON" >&2
  exit 1
fi

mkdir -p "$SHARE" "$BIN_DIR"
rm -rf "$SHARE/codex_relay"
cp -R "$ROOT/codex_relay" "$SHARE/codex_relay"
find "$SHARE/codex_relay" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
cp "$ROOT/README.md" "$SHARE/README.md" 2>/dev/null || true
rm -rf "$SHARE/Resources"
if [ -d "$ROOT/desktop/Resources" ]; then
  cp -R "$ROOT/desktop/Resources" "$SHARE/Resources"
elif [ -d "$ROOT/Resources" ]; then
  cp -R "$ROOT/Resources" "$SHARE/Resources"
fi

cat > "$BIN_DIR/codex-relay" <<EOF
#!/bin/sh
PYTHONPATH="$SHARE\${PYTHONPATH:+:\$PYTHONPATH}" exec "$PYTHON" -m codex_relay "\$@"
EOF
cat > "$BIN_DIR/codex-relay-gui" <<EOF
#!/bin/sh
PYTHONPATH="$SHARE\${PYTHONPATH:+:\$PYTHONPATH}" exec "$PYTHON" -m codex_relay.portable_gui "\$@"
EOF
chmod 755 "$BIN_DIR/codex-relay" "$BIN_DIR/codex-relay-gui"

echo "已安装: $BIN_DIR/codex-relay"
echo "数据目录: $HOME/.codex-relay"
echo "运行: codex-relay doctor"
echo "跨平台图形界面: $BIN_DIR/codex-relay-gui"
