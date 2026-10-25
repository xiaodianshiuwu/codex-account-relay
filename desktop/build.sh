#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
APP="${CODEX_RELAY_GUI_APP:-$HOME/Applications/Codex Relay.app}"
BUILD="$ROOT/build"
OPEN_AFTER="${CODEX_RELAY_OPEN_AFTER:-0}"
PYTHON="${CODEX_RELAY_PYTHON:-$(command -v python3 || true)}"

if [ -z "$PYTHON" ] || [ ! -x "$PYTHON" ]; then
  echo "找不到 Python 3：$PYTHON" >&2
  exit 1
fi

echo "==> 构建 Codex Relay GUI"
cd "$ROOT"
swift build -c release --product CodexRelay

echo "==> 准备 App bundle"
rm -rf "$BUILD"
mkdir -p "$BUILD/Codex Relay.app/Contents/MacOS"
mkdir -p "$BUILD/Codex Relay.app/Contents/Resources"
cp "$ROOT/Info.plist" "$BUILD/Codex Relay.app/Contents/Info.plist"
cp "$ROOT/.build/release/CodexRelay" "$BUILD/Codex Relay.app/Contents/MacOS/CodexRelay"
chmod 755 "$BUILD/Codex Relay.app/Contents/MacOS/CodexRelay"

# Ship the automation backend inside the app. A release download must not
# depend on `codex-relay` already existing in PATH.
CLI_BIN="${CODEX_RELAY_CLI_BIN:-$ROOT/../dist-cli/codex-relay}"
if [ -f "$CLI_BIN" ]; then
  cp "$CLI_BIN" "$BUILD/Codex Relay.app/Contents/Resources/codex-relay"
  chmod 755 "$BUILD/Codex Relay.app/Contents/Resources/codex-relay"
  echo "==> 已打包后端: $CLI_BIN"
else
  echo "==> 未找到打包后端，本地运行将回退到 PATH 中的 codex-relay" >&2
fi

if [ -f "$ROOT/Assets/AppIcon.icns" ]; then
  cp "$ROOT/Assets/AppIcon.icns" "$BUILD/Codex Relay.app/Contents/Resources/AppIcon.icns"
fi
if [ -d "$ROOT/Resources" ]; then
  cp -R "$ROOT/Resources/"*.lproj "$BUILD/Codex Relay.app/Contents/Resources/" 2>/dev/null || true
fi
if [ -f "$ROOT/../README.md" ]; then
  cp "$ROOT/../README.md" "$BUILD/Codex Relay.app/Contents/Resources/README.md"
fi

codesign --force --deep --sign - "$BUILD/Codex Relay.app" >/dev/null 2>&1 || true

echo "==> 安装到 $APP"
# Stop the previous menu-bar-only app or an older GUI build before replacing it.
osascript -e 'tell application id "io.github.xiaodianshiuwu.codexrelay" to quit' >/dev/null 2>&1 || true
osascript -e 'tell application id "io.github.xiaodianshiuwu.codexrelay" to quit' >/dev/null 2>&1 || true
pkill -f "$HOME/Applications/Codex Relay.app/Contents/MacOS/CodexRelay" >/dev/null 2>&1 || true
sleep 0.5
mkdir -p "$(dirname "$APP")"
rm -rf "$APP"
cp -R "$BUILD/Codex Relay.app" "$APP"

echo "已安装: $APP"
if [ "$OPEN_AFTER" = "1" ]; then
  open "$APP"
fi
echo "启动: open \"$APP\""
echo "自检: \"$APP/Contents/MacOS/CodexRelay\" --self-test"
