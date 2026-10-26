#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
APP="${CODEX_RELAY_GUI_APP:-}"
BUILD="${CODEX_RELAY_BUILD_DIR:-$ROOT/build}"
OPEN_AFTER="${CODEX_RELAY_OPEN_AFTER:-0}"
echo "==> 构建 Codex Relay GUI"
cd "$ROOT"
swift build -c release --product CodexRelay

echo "==> 准备 App bundle"
mkdir -p "$BUILD"
rm -rf "$BUILD/Codex Relay.app"
mkdir -p "$BUILD/Codex Relay.app/Contents/MacOS"
mkdir -p "$BUILD/Codex Relay.app/Contents/Resources"
cp "$ROOT/Info.plist" "$BUILD/Codex Relay.app/Contents/Info.plist"
cp "$ROOT/.build/release/CodexRelay" "$BUILD/Codex Relay.app/Contents/MacOS/CodexRelay"
chmod 755 "$BUILD/Codex Relay.app/Contents/MacOS/CodexRelay"

# Ship the automation backend inside the app. A release download must not
# depend on `codex-relay` already existing in PATH.
CLI_BIN="${CODEX_RELAY_CLI_BIN:-$ROOT/../dist-cli/codex-relay}"
if [ -x "$CLI_BIN" ]; then
  cp "$CLI_BIN" "$BUILD/Codex Relay.app/Contents/Resources/codex-relay"
  chmod 755 "$BUILD/Codex Relay.app/Contents/Resources/codex-relay"
  echo "==> 已打包后端: $CLI_BIN"
else
  echo "缺少可执行内置后端，请先运行 python tools/build_cli_bundle.py: $CLI_BIN" >&2
  exit 1
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

cp "$ROOT/../THIRD_PARTY_NOTICES.md" "$BUILD/Codex Relay.app/Contents/Resources/THIRD_PARTY_NOTICES.md"
cp "$ROOT/../LICENSE" "$BUILD/Codex Relay.app/Contents/Resources/LICENSE"
cp -R "$ROOT/../licenses" "$BUILD/Codex Relay.app/Contents/Resources/licenses"
# Strip Finder/resource-fork metadata from the generated bundle before signing.
xattr -cr "$BUILD/Codex Relay.app"
codesign --force --deep --sign - "$BUILD/Codex Relay.app"
codesign --verify --deep --strict "$BUILD/Codex Relay.app"

# Building never kills apps or launches automation. A destination is explicit.
if [ -n "$APP" ]; then
  if [ -e "$APP" ]; then
    echo "目标已存在，请使用新输出路径或手动升级：$APP" >&2
    exit 1
  fi
  mkdir -p "$(dirname "$APP")"
  ditto "$BUILD/Codex Relay.app" "$APP"
else
  APP="$BUILD/Codex Relay.app"
fi
echo "已构建: $APP"
if [ "$OPEN_AFTER" = "1" ]; then
  open "$APP"
fi
